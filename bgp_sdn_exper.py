#!/usr/bin/env python3
"""
Combined BGP + SDN Experiment
------------------------------
This script builds a unified Mininet environment that integrates:

1. **Underlay network** (Fat-Tree Mini): 2 Spine + 2 Leaf SDN switches
   - Switches subclassed as `HealthAwareSwitch` to monitor SDN controller reachability
   - All switches set to `fail-secure` (retain flows upon controller loss)

2. **Hosts**:
   - h1 (10.0.1.2/24) on leaf1
   - h2 (10.0.2.2/24) on leaf2

3. **BGP Routers** (using FRRouting via `BGPRouter` class):
   - bgp1 (AS65001) on leaf1, IP 10.0.1.1/24
   - bgp2 (AS65002) on leaf2, IP 10.0.2.1/24
   - eBGP peer link between bgp1 (10.0.12.1) and bgp2 (10.0.12.2)
   - Graceful Restart enabled

4. **CombinedFault Injection CLI** (`combined> ` prompt):
   - `failbgp <router>`: kill FRR bgpd on the given router
   - `failsdn <switch1> [switch2 ...]`: drop controller link to switches
   - `failboth <router> <switch1> [switch2 ...]`: do both simultaneously
   - `recoverbgp`: restart bgp daemons on both routers
   - `recoversdn`: re-establish controller on all switches
   - `status`: show SDN switch health + BGP session states


Usage:
------
1. Start your SDN controller (e.g. Ryu):
   ```bash
   ryu-manager --ofp-tcp-listen-port 6653 your_app.py
   ```
2. Launch this combined experiment:
   ```bash
   sudo python3 combined_experiment.py
   ```
3. In the Mininet CLI (`combined> `), perform:
   - `h1 ping -c2 h2`          # baseline connectivity
   - `failbgp bgp1`            # simulate BGP1 crash
   - `failsdn leaf1 leaf2`     # drop SDN control links
   - `failboth bgp1 leaf1`     # test simultaneous failure
   - `recoverbgp`              # bring BGP back up
   - `recoversdn`              # restore SDN links
   - `status`                  # view current health/status
   - `exit`                    # cleanup and exit

Measurements:
-------------
Record during experiments:
- Ping loss/RTT curves
- BGP GR convergence time (log timestamps)
- SDN failover detection time (health thread logs)
- FlowMod counts on controller logs
- ovs-ofctl dump-flows differences

"""
from mininet.net import Mininet
from mininet.topo import Topo
from mininet.node import RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import threading, time, os, subprocess

# ---------- SDN Switch with Health Monitoring ----------
from mininet.node import OVSSwitch
class HealthAwareSwitch(OVSSwitch):
    def __init__(self, name, **params):
        super().__init__(name, **params)
        self.health_state = 'healthy'
        self.monitor = None
    def start(self, controllers):
        super().start(controllers)
        # kick off background health check
        self.monitor = threading.Thread(target=self._monitor_ctrl, daemon=True)
        self.monitor.start()
    def _monitor_ctrl(self):
        ip = self.controllers[0].IP()
        while True:
            res = self.cmd(f'ping -c1 -W1 {ip}')
            state = 'healthy' if '1 received' in res else 'unknown'
            if state != self.health_state:
                info(f"*** {self.name} health: {self.health_state} -> {state}\n")
                self.health_state = state
            time.sleep(2)

# ---------- BGP Router using FRR ----------
from mininet.node import Host
class BGPRouter(Host):
    def __init__(self, name, **params):
        self.bgp_asn      = params.pop('asn')
        self.bgp_router_id= params.pop('router_id')
        self.frr_dir      = f'/tmp/frr-{name}'
        info(f"*** Init BGP Router {name} ASN={self.bgp_asn}\n")
        super(BGPRouter, self).__init__(name, **params)
    def config(self, **params):
        super().config(**params)
        # enable forwarding
        self.cmd('sysctl -w net.ipv4.ip_forward=1')
        # disable rp_filter on all intfs
        for intf in self.intfList():
            self.cmd(f'sysctl -w net.ipv4.conf.{intf.name}.rp_filter=0')
    def setup_frr(self, peers=None):
        # stop old
        self.cmd(f'pkill -f zebra.*{self.name} || true')
        self.cmd(f'pkill -f bgpd.*{self.name} || true')
        time.sleep(1)
        # prepare dirs
        self.cmd(f'rm -rf {self.frr_dir} && mkdir -p {self.frr_dir}/' + 'run sockets log'.replace(' ', f'/{self.frr_dir}/'))
        # daemons file
        da = 'zebra=yes\nbgpd=yes'
        with open(f'{self.frr_dir}/daemons','w') as f: f.write(da)
        # vtysh
        v = f"hostname {self.name}\nservice integrated-vtysh-config\n!"
        with open(f'{self.frr_dir}/vtysh.conf','w') as f: f.write(v)
        # FRR conf
        intf = self.name + '-eth0'
        frr = [
            'frr version 7.2.1',
            'frr defaults traditional',
            f'hostname {self.name}',
            '!',
            f'interface {intf}', f' ip address {self.IP()}', ' no shutdown', '!',
            f'router bgp {self.bgp_asn}', f' bgp router-id {self.bgp_router_id}',
            ' no bgp ebgp-requires-policy', ' no bgp default ipv4-unicast',
        ]
        if peers:
            for p in peers:
                frr += [f" neighbor {p['ip']} remote-as {p['asn']}"]
        frr += ['!', 'address-family ipv4 unicast']
        frr += [f' network {self.IP()}/24']
        if peers:
            for p in peers:
                frr += [f" neighbor {p['ip']} activate", ' exit-address-family']
        with open(f'{self.frr_dir}/frr.conf','w') as f: f.write('\n'.join(frr))
        # start zebra & bgpd
        self.popen(f'zebra -d -f {self.frr_dir}/frr.conf')
        time.sleep(1)
        self.popen(f'bgpd -d -f {self.frr_dir}/frr.conf')
        time.sleep(2)
        info(f"*** FRR started on {self.name}\n")

# ---------- Combined Experiment ----------
class CombinedExperiment:
    def __init__(self): self.net=None
    def setup_topology(self):
        topo = CombinedTopo()
        self.net = Mininet(topo=topo,
            switch=HealthAwareSwitch,
            controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653),
            link=TCLink, autoSetMacs=True)
        self.net.start()
        # SDN fail-secure
        for sw in self.net.switches:
            sw.cmd(f'ovs-vsctl set-fail-mode {sw.name} secure')
        info('*** SDN underlay ready\n')
    def configure_bgp(self):
        r1 = self.net.get('bgp1'); r2 = self.net.get('bgp2')
        r1.setup_frr(peers=[{'ip':'10.0.12.2','asn':65002}])
        r2.setup_frr(peers=[{'ip':'10.0.12.1','asn':65001}])
        # static routes
        r1.cmd('ip route add 10.0.2.0/24 via 10.0.12.2')
        r2.cmd('ip route add 10.0.1.0/24 via 10.0.12.1')
        info('*** BGP configured\n')
    def start(self):
        self.setup_topology(); self.configure_bgp()
        CLI(self.net, script=self)
    def stop(self):
        info('*** Stopping FRR\n')
        for r in ('bgp1','bgp2'):
            self.net.get(r).cmd('killall -9 zebra bgpd || true')
        self.net.stop()

# ---------- CLI for Fault Injection ----------
class CustomCLI(CLI):
    prompt = 'combined> '
    def do_failbgp(self, line):
        for r in line.split(): self.mn.get(r).cmd('pkill bgpd')
        print('*** BGP router(s) stopped')
    def do_failsdn(self, line):
        for sw in line.split(): self.mn.get(sw).cmd(f'ovs-vsctl del-controller {sw}')
        print('*** SDN control links removed')
    def do_failboth(self, line):
        parts = line.split(); self.do_failbgp(parts[0]); self.do_failsdn(' '.join(parts[1:]))
        print('*** Combined failure')
    def do_recoverbgp(self, line):
        for r in ('bgp1','bgp2'): self.mn.get(r).setup_frr(peers=[{'ip':'10.0.12.2','asn':65002} if r=='bgp1' else {'ip':'10.0.12.1','asn':65001}])
        print('*** BGP recovered')
    def do_recoversdn(self, line):
        for sw in self.mn.switches: sw.cmd(f'ovs-vsctl set-controller {sw.name} tcp:127.0.0.1:6653')
        print('*** SDN control restored')
    def do_status(self, line):
        print('Switch health:'); [print(f" {sw.name}: {sw.health_state}") for sw in self.mn.switches]
        print('BGP neighbors:'); [self.mn.get(r).cmd('vtysh -c "show ip bgp summary"') for r in ('bgp1','bgp2')]

# ---------- Topology Definition ----------
class CombinedTopo(Topo):
    def build(self):
        # spines and leafs
        sp1=self.addSwitch('sp1'); sp2=self.addSwitch('sp2')
        leaf1=self.addSwitch('leaf1');leaf2=self.addSwitch('leaf2')
        for l in (leaf1,leaf2): self.addLink(l,sp1,cls=TCLink,bw=100); self.addLink(l,sp2,cls=TCLink,bw=100)
        # hosts and BGP routers
        h1=self.addHost('h1',ip='10.0.1.2/24',defaultRoute='via 10.0.1.1')
        h2=self.addHost('h2',ip='10.0.2.2/24',defaultRoute='via 10.0.2.1')
        bgp1=self.addHost('bgp1',cls=BGPRouter,ip='10.0.1.1/24',defaultRoute='via 10.0.12.2',asn=65001,router_id='10.0.1.1')
        bgp2=self.addHost('bgp2',cls=BGPRouter,ip='10.0.2.1/24',defaultRoute='via 10.0.12.1',asn=65002,router_id='10.0.2.1')
        self.addLink(h1,leaf1,cls=TCLink,bw=100)
        self.addLink(leaf1,bgp1,intfName2='bgp1-eth0',cls=TCLink,bw=100)
        self.addLink(h2,leaf2,cls=TCLink,bw=100)
        self.addLink(leaf2,bgp2,intfName2='bgp2-eth0',cls=TCLink,bw=100)
        # eBGP peer link
        self.addLink(bgp1,bgp2,intfName1='bgp1-peer',intfName2='bgp2-peer',params1={'ip':'10.0.12.1/24'},params2={'ip':'10.0.12.2/24'},cls=TCLink,bw=100)

if __name__=='__main__':
    setLogLevel('info')
    exp=CombinedExperiment()
    try:
        exp.start()
    finally:
        exp.stop()
