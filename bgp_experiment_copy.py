#!/usr/bin/env python3

"""
BGP-SDN Experiment Environment
-----------------------------
This script creates two simulated datacenters with BGP routers using Mininet and FRRouting.
Each datacenter has a host, an OpenFlow switch, and a BGP router. The BGP routers peer with
each other to exchange routes.

Requirements:
- Mininet
- FRRouting (FRR)
- Open vSwitch
- Python 3.6+
"""

from mininet.net import Mininet
from mininet.node import Controller, RemoteController, OVSSwitch, Host
from mininet.cli import CLI
from mininet.log import setLogLevel, info, error
from mininet.link import TCLink
import os
import time
import subprocess
from pathlib import Path

class BGPRouter(Host):
    """Custom host class to configure FRR BGP routers"""
    
    def __init__(self, name, **params):
        """Initialize the BGP router with ASN"""
        self.bgp_asn = params.pop('asn')  # Store ASN before parent init
        self.bgp_router_id = params.pop('router_id')  # Store router_id before parent init
        self.frr_dir = f'/tmp/frr-{name}'  # Use /tmp/frr-{name} for per-router isolation
        info(f'*** Initializing BGP Router {name} with ASN {self.bgp_asn} and router ID {self.bgp_router_id}\n')
        print(f'[DEBUG][__init__] {name}: ASN={self.bgp_asn}, router_id={self.bgp_router_id}')
        super(BGPRouter, self).__init__(name, **params)
    
    def config(self, **params):
        print(f'[DEBUG][config] {self.name}: ASN={self.bgp_asn}, router_id={self.bgp_router_id}')
        super(BGPRouter, self).config(**params)
        info(f'*** Configuring BGP Router {self.name} with ASN {self.bgp_asn} and router ID {self.bgp_router_id}\n')
        # Enable IPv4 forwarding
        self.cmd('sysctl -w net.ipv4.ip_forward=1')
        # Disable reverse path filtering
        self.cmd('sysctl -w net.ipv4.conf.all.rp_filter=0')
        self.cmd('sysctl -w net.ipv4.conf.default.rp_filter=0')
        # Enable loose mode for reverse path filtering
        for intf in self.intfList():
            self.cmd(f'sysctl -w net.ipv4.conf.{intf.name}.rp_filter=0')
        
    def setup_frr(self, peers=None):
        """Configure FRR with BGP settings"""
        print(f'[DEBUG][setup_frr-start] {self.name}: ASN={self.bgp_asn}, router_id={self.bgp_router_id}, peers={peers}')
        info(f'*** Setting up FRR for {self.name} with ASN {self.bgp_asn} and router ID {self.bgp_router_id}\n')
        
        # Stop any existing FRR processes for this router
        self.cmd(f'pkill -f "zebra.*{self.name}"')
        self.cmd(f'pkill -f "bgpd.*{self.name}"')
        time.sleep(2)
        
        # Clean and create FRR directory
        self.cmd(f'rm -rf {self.frr_dir}')
        self.cmd(f'mkdir -p {self.frr_dir}')
        self.cmd(f'chown frr:frr {self.frr_dir}')
        
        # Create required subdirectories
        for subdir in ['run', 'log', 'sockets']:
            self.cmd(f'mkdir -p {self.frr_dir}/{subdir}')
            self.cmd(f'chown frr:frr {self.frr_dir}/{subdir}')
        
        # Generate daemons config
        daemons_conf = """zebra=yes
bgpd=yes
ospfd=no
ospf6d=no
ripd=no
ripngd=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no
vrrpd=no
pathd=no"""

        # Write daemons config
        with open(f'{self.frr_dir}/daemons', 'w') as f:
            f.write(daemons_conf)
        self.cmd(f'chmod 640 {self.frr_dir}/daemons')
        
        # Generate vtysh config
        vtysh_conf = f"""hostname {self.name}
username root nopassword
!
service integrated-vtysh-config
!
log file {self.frr_dir}/log/frr.log informational
"""
        
        # Write vtysh config
        with open(f'{self.frr_dir}/vtysh.conf', 'w') as f:
            f.write(vtysh_conf)
        self.cmd(f'chmod 644 {self.frr_dir}/vtysh.conf')
        
        # Generate integrated FRR config
        print(f'[DEBUG][setup_frr-preconf] {self.name}: ASN={self.bgp_asn}, router_id={self.bgp_router_id}, peers={peers}')
        frr_conf = f"""frr version 7.2.1
frr defaults traditional
!
hostname {self.name}
!
service integrated-vtysh-config
!
log timestamp precision 6
log file {self.frr_dir}/log/frr.log debugging
!
interface {self.name}-eth0
 description Connection to Switch
 ip address {self.IP()}/24
 no shutdown
!
interface {self.name}-peer
 description BGP Peering Link
 ip address {self.params['ip']}/24
 no shutdown
!
router bgp {self.bgp_asn}
 bgp router-id {self.bgp_router_id}
 bgp graceful-restart
 no bgp ebgp-requires-policy
 no bgp default ipv4-unicast
 no bgp network import-check
 bgp bestpath as-path multipath-relax
 timers bgp 3 9
"""
        if peers:
            for peer in peers:
                frr_conf += f" neighbor {peer['ip']} remote-as {peer['asn']}\n"
                frr_conf += f" neighbor {peer['ip']} description Peer with {peer['asn']}\n"
                frr_conf += f" neighbor {peer['ip']} timers 3 9\n"
                frr_conf += f" neighbor {peer['ip']} timers connect 5\n"
        
        frr_conf += "!\n address-family ipv4 unicast\n"
        frr_conf += f" network {self.IP()}/24\n"
        if peers:
            for peer in peers:
                frr_conf += f" neighbor {peer['ip']} activate\n"
                frr_conf += f" neighbor {peer['ip']} next-hop-self\n"
                frr_conf += f" neighbor {peer['ip']} soft-reconfiguration inbound\n"
        frr_conf += " maximum-paths 64\n"
        frr_conf += " redistribute connected\n"
        frr_conf += " exit-address-family\n!\n"
        
        # Add line vty config
        frr_conf += """!
line vty
!"""
        
        # Write FRR config
        with open(f'{self.frr_dir}/frr.conf', 'w') as f:
            f.write(frr_conf)
        
        # Set permissions
        self.cmd(f'chown -R frr:frr {self.frr_dir}')
        self.cmd(f'chmod 640 {self.frr_dir}/frr.conf')
        # Install kernel static route as a fail-static fallback
        for peer in peers or []:
            # derive remote /24 subnet from peer IP
            net = peer['ip'].rsplit('.', 1)[0] + '.0/24'
            self.cmd(f'ip route add {net} via {peer["ip"]}')
        
        # Create a custom vtysh.conf for this router
        vtysh_conf = f"""hostname {self.name}
username root nopassword
!
service integrated-vtysh-config
!
log file {self.frr_dir}/log/frr.log informational
"""
        with open(f'/etc/frr/vtysh-{self.name}.conf', 'w') as f:
            f.write(vtysh_conf)
        self.cmd(f'chmod 644 /etc/frr/vtysh-{self.name}.conf')
        
        # Start FRR daemons with namespace-aware configuration
        info(f'*** Starting FRR daemons for {self.name}\n')
        
        # Start Zebra with custom config
        zebra_cmd = f'/usr/lib/frr/zebra -d ' \
                    f'-f {self.frr_dir}/frr.conf ' \
                    f'-i {self.frr_dir}/run/zebra.pid ' \
                    f'-z {self.frr_dir}/sockets/zserv.api ' \
                    f'--vty_socket {self.frr_dir}/sockets ' \
                    f'--config_file {self.frr_dir}/frr.conf ' \
                    f'--pid_file {self.frr_dir}/run/zebra.pid ' \
                    f'--socket {self.frr_dir}/sockets/zserv.api ' \
                    f'--vty_addr 127.0.0.1 ' \
                    f'--vty_port 0'
        self.zebra = self.popen(zebra_cmd, shell=True)
        time.sleep(3)
        
        # Start BGPd with custom config
        bgpd_cmd = f'/usr/lib/frr/bgpd -d ' \
                   f'-f {self.frr_dir}/frr.conf ' \
                   f'-i {self.frr_dir}/run/bgpd.pid ' \
                   f'-z {self.frr_dir}/sockets/zserv.api ' \
                   f'--vty_socket {self.frr_dir}/sockets ' \
                   f'--config_file {self.frr_dir}/frr.conf ' \
                   f'--pid_file {self.frr_dir}/run/bgpd.pid ' \
                   f'--socket {self.frr_dir}/sockets/zserv.api ' \
                   f'--vty_addr 127.0.0.1 ' \
                   f'--vty_port 0'
        self.bgpd = self.popen(bgpd_cmd, shell=True)
        time.sleep(2)
        
        # Verify configuration and BGP status
        info(f'*** Verifying FRR configuration for {self.name}\n')
        self._verify_frr_status()
    
    def _verify_frr_status(self):
        """Verify FRR daemon status and configuration"""
        # Check if processes are running
        zebra_pid = self.cmd(f'cat {self.frr_dir}/run/zebra.pid').strip()
        bgpd_pid = self.cmd(f'cat {self.frr_dir}/run/bgpd.pid').strip()
        
        if not zebra_pid or not bgpd_pid:
            error(f'*** Error: FRR processes not running for {self.name}\n')
            return False
        
        # Verify process existence
        if not os.path.exists(f'/proc/{zebra_pid}') or not os.path.exists(f'/proc/{bgpd_pid}'):
            error(f'*** Error: FRR processes died for {self.name}\n')
            return False
        
        # Check BGP configuration using custom vtysh command
        info(f'*** BGP Configuration for {self.name}:\n')
        vtysh_cmd = f'VTYSH_PAGER=cat vtysh ' \
                    f'--config_dir {self.frr_dir} ' \
                    f'--vty_socket {self.frr_dir}/sockets ' \
                    f'-c "show running-config"'
        self.cmd(vtysh_cmd)
        
        # Check BGP status
        vtysh_cmd = f'VTYSH_PAGER=cat vtysh ' \
                    f'--config_dir {self.frr_dir} ' \
                    f'--vty_socket {self.frr_dir}/sockets ' \
                    f'-c "show ip bgp summary"'
        self.cmd(vtysh_cmd)
        
        # Check routing table
        info(f'*** Routing table for {self.name}:\n')
        self.cmd('ip route')
        
        return True

class SDNBGPExperiment:
    """Main class to set up and run the BGP experiment"""
    
    def __init__(self):
        self.net = None
        
    def setup_topology(self):
        """Create the network topology with two datacenters"""
        
        # Create network with RemoteController and fallback to default controller
        self.net = Mininet(
            topo=None,
            build=False,
            controller=None,
            switch=OVSSwitch,
            link=TCLink
        )
        
        info('*** Setting remote controller to 127.0.0.1:6653\n')
        c0 = self.net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
        
        # DC-A components
        s1 = self.net.addSwitch('s1')
        h1 = self.net.addHost('h1', ip='10.0.1.2/24', defaultRoute='via 10.0.1.1')
        bgp1 = self.net.addHost('bgp1', 
                               cls=BGPRouter,
                               ip='10.0.1.1/24',
                               defaultRoute='via 10.0.12.2',
                               asn=65001,
                               router_id='10.0.1.1')  # AS 65001
        
        # DC-B components
        s2 = self.net.addSwitch('s2')
        h2 = self.net.addHost('h2', ip='10.0.2.2/24', defaultRoute='via 10.0.2.1')
        bgp2 = self.net.addHost('bgp2',
                               cls=BGPRouter,
                               ip='10.0.2.1/24',
                               defaultRoute='via 10.0.12.1',
                               asn=65002,
                               router_id='10.0.2.1')  # AS 65002
        
        # Add links with explicit MAC addresses to avoid conflicts
        # DC-A internal links
        self.net.addLink(h1, s1, addr1="00:00:00:00:01:02")
        self.net.addLink(s1, bgp1, intfName2='bgp1-eth0', addr2="00:00:00:00:01:01")
        
        # DC-B internal links
        self.net.addLink(h2, s2, addr1="00:00:00:00:02:02")
        self.net.addLink(s2, bgp2, intfName2='bgp2-eth0', addr2="00:00:00:00:02:01")
        
        # eBGP peering link
        self.net.addLink(bgp1, bgp2,
                        intfName1='bgp1-peer', intfName2='bgp2-peer',
                        params1={'ip': '10.0.12.1/24'},
                        params2={'ip': '10.0.12.2/24'},
                        addr1="00:00:00:00:12:01",
                        addr2="00:00:00:00:12:02")
        
        # Build and start network
        self.net.build()
        self.net.start()
        
        # Start switches
        s1.start([c0])
        s2.start([c0])
        
        # Wait for network to stabilize
        info('*** Waiting for network to stabilize\n')
        time.sleep(5)
        
        # Configure flow tables with specific rules for known routes
        for switch in [s1, s2]:
            switch.cmd('ovs-vsctl set bridge {} protocols=OpenFlow13'.format(switch.name))
            switch.cmd('ovs-ofctl -O OpenFlow13 del-flows {}'.format(switch.name))
            
            # ARP packets: flood
            switch.cmd('ovs-ofctl -O OpenFlow13 add-flow {} "table=0,priority=100,dl_type=0x0806,actions=FLOOD"'.format(switch.name))
            
            # ICMP packets: forward based on destination
            if switch.name == 's1':
                # s1: forward to h1 or bgp1 based on destination
                switch.cmd('ovs-ofctl -O OpenFlow13 add-flow {} "table=0,priority=100,dl_type=0x0800,nw_dst=10.0.1.2,actions=output:1"'.format(switch.name))
                switch.cmd('ovs-ofctl -O OpenFlow13 add-flow {} "table=0,priority=100,dl_type=0x0800,nw_dst=10.0.1.1,actions=output:2"'.format(switch.name))
                switch.cmd('ovs-ofctl -O OpenFlow13 add-flow {} "table=0,priority=50,dl_type=0x0800,actions=output:2"'.format(switch.name))
            else:
                # s2: forward to h2 or bgp2 based on destination
                switch.cmd('ovs-ofctl -O OpenFlow13 add-flow {} "table=0,priority=100,dl_type=0x0800,nw_dst=10.0.2.2,actions=output:1"'.format(switch.name))
                switch.cmd('ovs-ofctl -O OpenFlow13 add-flow {} "table=0,priority=100,dl_type=0x0800,nw_dst=10.0.2.1,actions=output:2"'.format(switch.name))
                switch.cmd('ovs-ofctl -O OpenFlow13 add-flow {} "table=0,priority=50,dl_type=0x0800,actions=output:2"'.format(switch.name))

        # Configure BGP routers
        info('*** Configuring BGP routers\n')
        # Configure BGP for router 1 (AS 65001)
        bgp1.setup_frr(
            peers=[{
                'ip': '10.0.12.2',
                'asn': 65002
            }]
        )
        
        # Configure BGP for router 2 (AS 65002)
        bgp2.setup_frr(
            peers=[{
                'ip': '10.0.12.1',
                'asn': 65001
            }]
        )
        
        # Add static routes for direct networks
        bgp1.cmd('ip route add 10.0.2.0/24 via 10.0.12.2')
        bgp2.cmd('ip route add 10.0.1.0/24 via 10.0.12.1')
        
        # Verify connectivity between BGP routers
        info('*** Verifying BGP router connectivity\n')
        bgp1.cmd('ping -c 1 10.0.12.2')
        bgp2.cmd('ping -c 1 10.0.12.1')
        
        self.net.experiment = self  # Attach the experiment instance to the Mininet object
        
    def configure_bgp(self):
        """Configure BGP on the routers"""
        
        # Configure BGP for Router 1 (AS 65001)
        self.net.get('bgp1').setup_frr(
            peers=[{'ip': '10.0.12.2', 'asn': 65002}]
        )
        
        # Configure BGP for Router 2 (AS 65002)
        self.net.get('bgp2').setup_frr(
            peers=[{'ip': '10.0.12.1', 'asn': 65001}]
        )
        
        # Wait for BGP to establish
        info('*** Waiting for BGP to establish\n')
        time.sleep(10)
        
        # Verify BGP status
        info('*** Verifying BGP status\n')
        for router in ['bgp1', 'bgp2']:
            info(f'*** {router} BGP status:\n')
            self.net.get(router).cmd('vtysh -c "show ip bgp summary"')
            self.net.get(router).cmd('vtysh -c "show ip route"')
            self.net.get(router).cmd('vtysh -c "show ip bgp neighbors"')
        
        # Verify connectivity
        info('*** Verifying connectivity\n')
        result = self.net.get('h1').cmd('ping -c 1 10.0.2.2')
        if '1 received' not in result:
            error('*** Warning: Initial connectivity test failed\n')
        
    def start_experiment(self):
        """Start the experiment environment"""
        self.setup_topology()
        self.configure_bgp()
        
        info('*** Network is ready\n')
        info('*** To test connectivity:\n')
        info('    h1 ping h2\n')
        info('*** To check BGP status (use explicit config_dir and vty_socket!):\n')
        info('    bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show ip bgp summary"\n')
        info('    bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show ip bgp summary"\n')
        info('*** To check BGP neighbors:\n')
        info('    bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show ip bgp neighbors"\n')
        info('    bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show ip bgp neighbors"\n')
        info('*** To check FRR status:\n')
        info('    bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show running-config"\n')
        info('*** To verify routes:\n')
        info('    bgp1 ip route\n')
        info('*** To check BGP routes:\n')
        info('    bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show ip bgp"\n')
        info('*** To check BGP debugging:\n')
        info('    bgp1 tail -f /tmp/frr-bgp1/log/frr.log\n')
        info('*** To check switch flows:\n')
        info('    s1 ovs-ofctl -O OpenFlow13 dump-flows s1\n')
        
        # Start CLI
        CustomCLI(self.net)
        
    def stop_experiment(self):
        """Clean up the experiment"""
        if self.net:
            info('*** Stopping FRR daemons\n')
            for router in ['bgp1', 'bgp2']:
                if router in self.net:
                    self.net.get(router).cmd('killall -9 watchfrr zebra bgpd || true')
            info('*** Stopping network\n')
            self.net.stop()

    def recover_bgp_after_link_flap(self):
        """
        Automate BGP recovery after a link flap by flushing route cache and restarting FRR daemons.
        """
        for router in ['bgp1', 'bgp2']:
            r = self.net.get(router)
            info(f'*** Flushing route cache on {router}\n')
            r.cmd('ip route flush cache')
            info(f'*** Restarting FRR daemons on {router}\n')
            r.cmd('pkill bgpd; pkill zebra; pkill staticd; sleep 1')
            # Optionally, re-run setup_frr to re-apply config and restart daemons
            if hasattr(r, 'setup_frr'):
                # You may want to pass the correct peers again
                if router == 'bgp1':
                    r.setup_frr(peers=[{'ip': '10.0.12.2', 'asn': 65002}])
                else:
                    r.setup_frr(peers=[{'ip': '10.0.12.1', 'asn': 65001}])
            info(f'*** BGP recovery complete for {router}\n')
        info('*** BGP recovery after link flap complete!\n')

class CustomCLI(CLI):
    def do_recoverbgp(self, line):
        """
        recoverbgp
        Run BGP recovery after a link flap (flush route cache, restart FRR daemons, re-apply config).
        Usage: recoverbgp
        """
        if hasattr(self.mn, 'experiment') and hasattr(self.mn.experiment, 'recover_bgp_after_link_flap'):
            self.mn.experiment.recover_bgp_after_link_flap()
        else:
            print("*** Error: Experiment object with recovery method not found.")

    def do_startfrr(self, line):
        """
        startfrr
        Start zebra and bgpd daemons for both bgp1 and bgp2 with correct config and socket paths.
        Usage: startfrr
        """
        for router in ['bgp1', 'bgp2']:
            try:
                r = self.mn.get(router)
                print(f"*** Starting zebra and bgpd for {router}")
                frr_dir = f"/tmp/frr-{router}"
                # Clean up old run/sockets files
                r.cmd(f'rm -rf {frr_dir}/run/*')
                r.cmd(f'rm -rf {frr_dir}/sockets/*')
                # Start zebra
                zebra_cmd = f'/usr/lib/frr/zebra -d -f {frr_dir}/frr.conf -i {frr_dir}/run/zebra.pid -z {frr_dir}/sockets/zserv.api --vty_socket {frr_dir}/sockets --config_file {frr_dir}/frr.conf --pid_file {frr_dir}/run/zebra.pid --socket {frr_dir}/sockets/zserv.api --vty_addr 127.0.0.1 --vty_port 0'
                r.cmd(zebra_cmd)
                time.sleep(2)
                # Start bgpd
                bgpd_cmd = f'/usr/lib/frr/bgpd -d -f {frr_dir}/frr.conf -i {frr_dir}/run/bgpd.pid -z {frr_dir}/sockets/zserv.api --vty_socket {frr_dir}/sockets --config_file {frr_dir}/frr.conf --pid_file {frr_dir}/run/bgpd.pid --socket {frr_dir}/sockets/zserv.api --vty_addr 127.0.0.1 --vty_port 0'
                r.cmd(bgpd_cmd)
                print(f"*** FRR daemons started for {router}")
            except Exception as e:
                print(f"*** Error starting FRR daemons for {router}: {e}")

def main():
    """Main function to run the experiment"""
    setLogLevel('info')
    
    experiment = SDNBGPExperiment()
    try:
        experiment.start_experiment()
    finally:
        experiment.stop_experiment()

if __name__ == '__main__':
    main() 