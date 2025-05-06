# BGP-SDN Experiment Environment

This project provides a Mininet-based environment for experimenting with BGP routing in an SDN-controlled network. It simulates two datacenters, each with its own host, OpenFlow switch, and BGP router, allowing for BGP peering and route exchange studies.

## Architecture

```
DC-A                          DC-B
[h1] --- [s1] --- [bgp1] === [bgp2] --- [s2] --- [h2]
10.0.1.2    |     10.0.1.1   10.0.2.1    |     10.0.2.2
            |     AS 65001    AS 65002    |
            |                             |
            +-------- [SDN Controller] ----+
                         (c0)
```

## Prerequisites

1. Operating System:
   - Ubuntu 20.04 LTS or newer
   - Root/sudo access

2. Required Software:
   - Python 3.6 or newer
   - Mininet
   - Open vSwitch
   - FRRouting (FRR)

## Installation

1. Install system dependencies:
```bash
sudo apt-get update
sudo apt-get install -y mininet python3-pip frr
```

2. Enable and start FRR services:
```bash
sudo systemctl enable frr
sudo systemctl start frr
```

3. Configure FRR to allow BGP:
```bash
sudo sed -i 's/bgpd=no/bgpd=yes/' /etc/frr/daemons
sudo systemctl restart frr
```

## Usage

1. Start the experiment:
```bash
sudo python3 bgp_sdn_experiment.py
```

2. Test connectivity:
```bash
mininet> h1 ping h2
```

3. Check BGP status (use explicit config_dir and vty_socket!):
```bash
mininet> bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show ip bgp summary"
mininet> bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show ip bgp summary"
```

4. Check BGP neighbors:
```bash
mininet> bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show ip bgp neighbors"
mininet> bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show ip bgp neighbors"
```

5. Check FRR running config:
```bash
mininet> bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show running-config"
mininet> bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show running-config"
```

6. Exit the environment:
```bash
mininet> exit
```

7. Recover BGP after a link flap (custom CLI command):
```bash
mininet> recoverbgp
```

8. Manually start FRR daemons if needed (custom CLI command):
```bash
mininet> startfrr
```

## Testing Scenarios

1. Basic Connectivity:
   - Verify ping between h1 and h2
   - Check BGP route advertisement:
     ```bash
     mininet> bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show ip bgp"
     mininet> bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show ip bgp"
     ```
   - Examine BGP neighbor status:
     ```bash
     mininet> bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show ip bgp neighbors"
     mininet> bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show ip bgp neighbors"
     ```

2. Failure Testing:
   - Break BGP peering: `mininet> link bgp1 bgp2 down`
   - Restore BGP peering: `mininet> link bgp1 bgp2 up`

## Troubleshooting

1. If BGP doesn't establish or you see 'failed to connect to any daemons':
   - Use the custom CLI command to manually start FRR daemons:
     ```bash
     mininet> startfrr
     ```
   - Use the custom CLI command to recover BGP after a link flap:
     ```bash
     mininet> recoverbgp
     ```
   - Check FRR service status: `sudo systemctl status frr`
   - Verify BGP configuration (use explicit config_dir and vty_socket!):
     ```bash
     mininet> bgp1 vtysh --config_dir /tmp/frr-bgp1 --vty_socket /tmp/frr-bgp1/sockets -c "show running-config"
     mininet> bgp2 vtysh --config_dir /tmp/frr-bgp2 --vty_socket /tmp/frr-bgp2/sockets -c "show running-config"
     ```
   - Ensure IP forwarding is enabled: `sysctl net.ipv4.ip_forward`
   - **Note:** Always use the explicit `--config_dir` and `--vty_socket` options with vtysh in Mininet to avoid reading the wrong config or connecting to the wrong daemon.

2. If hosts can't communicate:
   - Check switch connectivity: `mininet> net`
   - Verify IP addresses: `mininet> dump`
   - Examine routes: `mininet> h1 ip route`

## License

MIT License

## Contributing

Feel free to submit issues and enhancement requests! 