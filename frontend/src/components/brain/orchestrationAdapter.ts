export type AgentStepStatus = "waiting" | "running" | "done" | "blocked";

export interface AgentStep {
  id: string;
  agentName: string;
  role: string;
  identity: string;
  status: AgentStepStatus;
  summary: string;
  detail: string[];
  outputName: string;
}

export interface AgentHandoffMessage {
  id: string;
  text: string;
  afterStepIndex: number;
}

export interface OrchestrationSession {
  goal: string;
  intro: string;
  handoffs: AgentHandoffMessage[];
  steps: AgentStep[];
}

export interface AgentOrchestrationAdapter {
  createPreviewSession: (goal: string) => OrchestrationSession;
}

export const previewOrchestrationAdapter: AgentOrchestrationAdapter = {
  createPreviewSession(goal) {
    return {
      goal,
      intro: "收到。我会先把目标拆成可执行 Brief，再按账号定位、内容策略、选题、脚本和运营动作依次调用专家。",
      handoffs: [
        {
          id: "handoff-positioning",
          afterStepIndex: 0,
          text: "好的，我先调用账号定位专家，对账号定位与账号状态进行分析。",
        },
        {
          id: "handoff-strategy",
          afterStepIndex: 1,
          text: "账号定位专家已完成定位分析，接下来交给内容策略专家处理。",
        },
        {
          id: "handoff-topic",
          afterStepIndex: 2,
          text: "内容策略已收敛，我继续调用选题专家生成可执行选题方向。",
        },
        {
          id: "handoff-script",
          afterStepIndex: 3,
          text: "选题方向已完成，接下来交给脚本专家形成首轮内容 Brief。",
        },
        {
          id: "handoff-operation",
          afterStepIndex: 4,
          text: "脚本专家已完成首轮内容骨架，最后交给账号运营专家判断发布与复盘动作。",
        },
      ],
      steps: [
        {
          id: "positioning",
          agentName: "账号定位专家",
          role: "账号定位 / 授权状态 / 人设边界",
          identity: "定位",
          status: "waiting",
          outputName: "账号定位约束",
          summary: "建议先按账号定位与授权状态确定执行边界，不直接进入批量内容生产。",
          detail: [
            "检查账号是否已授权、是否具备基础数据回流。",
            "识别账号组的人设、赛道和当前内容目标是否一致。",
            "输出账号定位约束，供后续内容策略使用。",
          ],
        },
        {
          id: "strategy",
          agentName: "内容策略专家",
          role: "目标拆解 / 冷启动策略 / 风险边界",
          identity: "策略",
          status: "waiting",
          outputName: "阶段性内容策略",
          summary: "本轮应先做低风险冷启动，避免一开始追求全量分发。",
          detail: [
            "拆解目标周期、内容目标和可执行平台范围。",
            "确定本轮内容主线与风险边界。",
            "形成可确认的任务 Brief。",
          ],
        },
        {
          id: "topic",
          agentName: "选题专家",
          role: "选题池 / 优先级 / 平台适配",
          identity: "选题",
          status: "waiting",
          outputName: "首轮选题池",
          summary: "首轮建议保留 3 个选题方向，优先验证账号定位是否成立。",
          detail: [
            "根据账号定位生成选题池。",
            "按可拍摄性、平台适配和风险等级排序。",
            "把选题交给脚本专家继续细化。",
          ],
        },
        {
          id: "script",
          agentName: "脚本专家",
          role: "脚本结构 / 表达风险 / 制作 Brief",
          identity: "脚本",
          status: "waiting",
          outputName: "脚本骨架",
          summary: "脚本需要先过合规质量门，再进入视频制作或分发。",
          detail: [
            "输出开头钩子、核心观点、转场和结尾动作。",
            "标记可能触发平台审核的表达。",
            "形成可验收的脚本交付物。",
          ],
        },
        {
          id: "operation",
          agentName: "账号运营专家",
          role: "发布动作 / 数据回流 / 复盘节点",
          identity: "运营",
          status: "waiting",
          outputName: "发布与复盘动作",
          summary: "先同步账号资料与内容基础数据，再生成发布计划和复盘指标。",
          detail: [
            "检查抖音授权和平台接入状态。",
            "规划发布后需要回流的数据指标。",
            "把复盘建议回写给运营大脑。",
          ],
        },
      ],
    };
  },
};
