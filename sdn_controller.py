#!/usr/bin/env python3
"""
Orion-like Ryu Controller for Fail-Closed / Fail-Static

- capacity_degradation_threshold: 失联 spine 数量 / 总 spine 数量
  如果 < threshold → Fail-Closed（重新下发流表，绕开失联 spine）
  如果 >= threshold → Fail-Static（保持现有流表，不再重下发）

示例中我们硬编码了 2 个 spine，2 个 leaf，2 个 host h1↔h2。
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4
import networkx as nx

class OrionController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # 失联检测阈值（2 spine 中失联一个仍为 Fail-Closed，两个失联则 Fail-Static）
    capacity_degradation_threshold = 0.5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 使用 NetworkX 构造简单拓扑图
        self.net = nx.Graph()
        # spine 节点
        spines = ['sp1', 'sp2']
        leafs = ['l1', 'l2']
        # 建图：leaf–spine
        for sp in spines:
            for lf in leafs:
                self.net.add_edge(sp, lf)
        # 监测各节点连接状态
        self.alive_spines = set(spines)
        # 记录 dpid -> name
        self.datapaths = {}
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        dpid = dp.id
        name = dp.ofproto.OFPT_HELLO  # placeholder, 实际应通过配置映射
        # 简化：假设 dpid 1/2 为 sp1/sp2，其它为 leaf
        if dpid in (1,2):
            sp_name = f"sp{dpid}"
            if ev.state == MAIN_DISPATCHER:
                if sp_name not in self.alive_spines:
                    self.alive_spines.add(sp_name)
                    self.logger.info("*** Spine %s reconnected", sp_name)
                    self._recompute_routes()
            elif ev.state == DEAD_DISPATCHER:
                if sp_name in self.alive_spines:
                    self.alive_spines.remove(sp_name)
                    self.logger.info("*** Spine %s LOST", sp_name)
                    self._recompute_routes()

    def _recompute_routes(self):
        total = 2
        failed = total - len(self.alive_spines)
        ratio = failed / total
        if ratio < self.capacity_degradation_threshold:
            # Fail-Closed：重新安装绕过失联 spine 的单路径流表
            self.logger.info("=== Fail-Closed (failed=%d) ===", failed)
            self._install_unicast_via(self.alive_spines)
        else:
            # Fail-Static：不再变动流表
            self.logger.info("=== Fail-Static (failed=%d) ===", failed)
            # nothing

    def _install_unicast_via(self, spines):
        """
        简化：演示如何下发流表到 leaf 交换机
        真实场景需获取端口映射并安装流表
        """
        self.logger.info("Installing new path via %s", list(spines))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        启动时，为 switch 下发默认流表：ARP flood，IP flood
        """
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        # 清空表
        mod = parser.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE)
        dp.send_msg(mod)
        # table=0, priority=0: flood
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        fm = parser.OFPFlowMod(datapath=dp, priority=0, match=match, instructions=inst)
        dp.send_msg(fm)