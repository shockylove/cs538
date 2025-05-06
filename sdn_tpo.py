#!/usr/bin/env python3
"""
Orion SDN Topology for Fail-Static Testing

Topology:
               Controller (c0)
                    |
           +--------+--------+
           |                 |
         sp1               sp2       # Spine 交换机
           |                 |
       +---+---+         +---+---+
       |       |         |       |
     leaf1   leaf2     leaf1   leaf2  # Leaf 交换机（名称相同，用不同 DPID）
      |  \     |  \     |  \     |  \
     h1  h2   h3  h4   h1  h2   h3  h4   # 两个叶节点，每个叶节点连两台 Host

每条 leaf–spine–leaf 路径都在两个 spine 上冗余。
"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

class OrionTopo(Topo):
    def build(self):
        # 两台 Spine
        spine1 = self.addSwitch('sp1')
        spine2 = self.addSwitch('sp2')

        # 两台 Leaf
        leafs = [self.addSwitch('l1'), self.addSwitch('l2')]

        # Spine ↔ Leaf
        for leaf in leafs:
            self.addLink(leaf, spine1, cls=TCLink, bw=100)
            self.addLink(leaf, spine2, cls=TCLink, bw=100)

        # 每个 Leaf 连 2 台 Host
        host_id = 1
        for leaf in leafs:
            for _ in range(2):
                host = self.addHost(f'h{host_id}',
                                     ip=f'10.0.{host_id}.2/24',
                                     defaultRoute=f'via 10.0.{host_id}.1')
                # 默认端口映射自动分配
                self.addLink(host, leaf, cls=TCLink, bw=100)
                host_id += 1

if __name__ == '__main__':
    setLogLevel('info')
    topo = OrionTopo()
    net = Mininet(
        topo=topo,
        switch=OVSSwitch,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653),
        link=TCLink,
        autoSetMacs=True
    )
    net.start()

    info('*** 设置所有交换机为 Fail-Secure 模式 (控制器失联时保留流表)\n')
    for sw in net.switches:
        sw.cmd(f'ovs-vsctl set-fail-mode {sw.name} secure')

    info('*** 拓扑就绪，进入 CLI ***\n')
    CLI(net)
    net.stop()