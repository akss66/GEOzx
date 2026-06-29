// 高仿真演示数据（无后端部分的支撑）。数值固定，保证演示稳定可复现。

export type StageKey =
  | "positioning"
  | "content_direction"
  | "art_direction"
  | "video_creation"
  | "editing"
  | "operation"
  | "advertising"
  | "customer_service";

export interface Stage {
  key: StageKey;
  index: string;
  name: string;
  agent: string;
}

export const STAGES: Stage[] = [
  { key: "positioning", index: "01", name: "账号定位", agent: "定位专家" },
  { key: "content_direction", index: "02", name: "编导文案", agent: "编导专家" },
  { key: "art_direction", index: "03", name: "美术提示词", agent: "美术指导" },
  { key: "video_creation", index: "04", name: "视频创作", agent: "视频专家" },
  { key: "editing", index: "05", name: "剪辑", agent: "剪辑专家" },
  { key: "operation", index: "06", name: "运营分发", agent: "运营专家" },
  { key: "advertising", index: "07", name: "投流", agent: "投流专家" },
  { key: "customer_service", index: "08", name: "客服", agent: "客服专家" },
];

export type Platform = "douyin" | "xiaohongshu" | "shipinhao";
export const PLATFORM_LABEL: Record<Platform, string> = {
  douyin: "抖音",
  xiaohongshu: "小红书",
  shipinhao: "视频号",
};

export type CardStatus = "running" | "done" | "blocked" | "review";

export interface ContentCard {
  id: number;
  title: string;
  account: string;
  platform: Platform;
  stage: StageKey;
  status: CardStatus;
  version: number;
  cost: number;
  updated: string;
  gate?: string;
}

export const CONTENT_CARDS: ContentCard[] = [
  { id: 1042, title: "618 新品开箱：三分钟看懂值不值", account: "数码菌", platform: "douyin", stage: "editing", status: "review", version: 3, cost: 0.42, updated: "12 分钟前", gate: "成片审核" },
  { id: 1041, title: "通勤穿搭，预算三百搞定一周", account: "穿搭日记", platform: "xiaohongshu", stage: "operation", status: "running", version: 2, cost: 0.31, updated: "8 分钟前" },
  { id: 1039, title: "租房改造 Before/After，回本攻略", account: "小户型研究所", platform: "douyin", stage: "video_creation", status: "running", version: 1, cost: 0.55, updated: "21 分钟前" },
  { id: 1038, title: "厨房好物 Top5，实测翻车与真香", account: "好物雷达", platform: "douyin", stage: "content_direction", status: "blocked", version: 2, cost: 0.18, updated: "34 分钟前", gate: "脚本合规" },
  { id: 1036, title: "周末徒步路线，新手友好不踩坑", account: "城市漫游", platform: "shipinhao", stage: "positioning", status: "done", version: 1, cost: 0.09, updated: "1 小时前" },
  { id: 1035, title: "国货护肤平替，敏感肌实测两周", account: "成分控", platform: "xiaohongshu", stage: "art_direction", status: "running", version: 1, cost: 0.27, updated: "1 小时前" },
  { id: 1033, title: "百元机皇还是智商税？深度长测", account: "数码菌", platform: "douyin", stage: "editing", status: "done", version: 2, cost: 0.61, updated: "2 小时前" },
  { id: 1031, title: "一周减脂餐，三步搞定不挨饿", account: "轻食研究室", platform: "xiaohongshu", stage: "content_direction", status: "running", version: 1, cost: 0.14, updated: "3 小时前" },
  { id: 1029, title: "宠物自动喂食器横评，别再交学费", account: "好物雷达", platform: "douyin", stage: "operation", status: "blocked", version: 4, cost: 0.73, updated: "3 小时前", gate: "发布前审核" },
  { id: 1027, title: "桌搭清单：从乱到爽的生产力升级", account: "效率玩家", platform: "shipinhao", stage: "video_creation", status: "running", version: 1, cost: 0.38, updated: "4 小时前" },
  { id: 1024, title: "平价咖啡机谁更香？盲测对比", account: "好物雷达", platform: "douyin", stage: "positioning", status: "done", version: 1, cost: 0.08, updated: "5 小时前" },
  { id: 1021, title: "新手化妆五分钟出门妆，手残友好", account: "成分控", platform: "xiaohongshu", stage: "editing", status: "review", version: 2, cost: 0.29, updated: "6 小时前", gate: "成片审核" },
];

export interface Gate {
  id: number;
  contentId: number;
  title: string;
  account: string;
  gate: "脚本合规" | "成片审核" | "发布前审核" | "大额投放";
  forced: boolean;
  waiting: string;
  risk?: string;
}

export const PENDING_GATES: Gate[] = [
  { id: 91, contentId: 1038, title: "厨房好物 Top5，实测翻车与真香", account: "好物雷达", gate: "脚本合规", forced: true, waiting: "34 分钟", risk: "疑似绝对化用语「最」" },
  { id: 90, contentId: 1029, title: "宠物自动喂食器横评，别再交学费", account: "好物雷达", gate: "发布前审核", forced: true, waiting: "3 小时", risk: "封面含未授权 logo" },
  { id: 89, contentId: 1042, title: "618 新品开箱：三分钟看懂值不值", account: "数码菌", gate: "成片审核", forced: false, waiting: "12 分钟" },
  { id: 88, contentId: 1055, title: "千川计划：开学季数码专场", account: "数码菌", gate: "大额投放", forced: true, waiting: "1 小时", risk: "日预算 ¥3,200 > 阈值 ¥2,000" },
];

export interface Activity {
  id: number;
  time: string;
  type: "agent" | "gate" | "publish" | "optimize" | "ad";
  text: string;
}

export const ACTIVITY: Activity[] = [
  { id: 1, time: "刚刚", type: "agent", text: "剪辑专家 完成《618 新品开箱》成片 v3，等待成片审核" },
  { id: 2, time: "8 分钟前", type: "publish", text: "《通勤穿搭》已发布到 小红书 @穿搭日记，回流播放 1.2 万" },
  { id: 3, time: "21 分钟前", type: "gate", text: "脚本合规门拦截《厨房好物 Top5》——疑似绝对化用语，待审核员处理" },
  { id: 4, time: "1 小时前", type: "optimize", text: "运营专家 广播优化建议：完播率低于 30% 的内容统一前置 3 秒钩子" },
  { id: 5, time: "2 小时前", type: "ad", text: "投流专家 追投《百元机皇长测》，ROI 2.4，消耗 ¥640" },
  { id: 6, time: "3 小时前", type: "agent", text: "定位专家 更新 @城市漫游 定位策略，差异化建议 4 条" },
];

// —— 账号矩阵 ——

export interface AccountGroup {
  id: number;
  name: string;
  dimension: "赛道" | "人设" | "平台";
}
export const ACCOUNT_GROUPS: AccountGroup[] = [
  { id: 1, name: "数码科技", dimension: "赛道" },
  { id: 2, name: "时尚美妆", dimension: "赛道" },
  { id: 3, name: "生活方式", dimension: "赛道" },
  { id: 4, name: "好物种草", dimension: "人设" },
];

export interface AccountRow {
  id: number;
  nickname: string;
  platform: Platform;
  group: string;
  followers: number;
  status: "active" | "inactive" | "banned";
  posts7d: number;
  avgPlay: number;
}

export const ACCOUNTS: AccountRow[] = [
  { id: 1, nickname: "数码菌", platform: "douyin", group: "数码科技", followers: 482000, status: "active", posts7d: 6, avgPlay: 86000 },
  { id: 2, nickname: "好物雷达", platform: "douyin", group: "好物种草", followers: 311000, status: "active", posts7d: 9, avgPlay: 64000 },
  { id: 3, nickname: "穿搭日记", platform: "xiaohongshu", group: "时尚美妆", followers: 156000, status: "active", posts7d: 7, avgPlay: 23000 },
  { id: 4, nickname: "成分控", platform: "xiaohongshu", group: "时尚美妆", followers: 98000, status: "active", posts7d: 5, avgPlay: 18000 },
  { id: 5, nickname: "小户型研究所", platform: "douyin", group: "生活方式", followers: 207000, status: "active", posts7d: 4, avgPlay: 51000 },
  { id: 6, nickname: "城市漫游", platform: "shipinhao", group: "生活方式", followers: 73000, status: "active", posts7d: 3, avgPlay: 12000 },
  { id: 7, nickname: "效率玩家", platform: "shipinhao", group: "数码科技", followers: 61000, status: "active", posts7d: 4, avgPlay: 9800 },
  { id: 8, nickname: "轻食研究室", platform: "xiaohongshu", group: "生活方式", followers: 134000, status: "active", posts7d: 6, avgPlay: 21000 },
  { id: 9, nickname: "数码菌·小号", platform: "douyin", group: "数码科技", followers: 28000, status: "inactive", posts7d: 0, avgPlay: 4200 },
  { id: 10, nickname: "好物雷达·测评号", platform: "douyin", group: "好物种草", followers: 45000, status: "active", posts7d: 8, avgPlay: 15000 },
];

// —— 知识库 ——

export interface KnowledgeItem {
  id: number;
  category: "hot_content" | "user_persona" | "prompt_library" | "script_library";
  title: string;
  tag: string;
  metric: string;
}
export const KNOWLEDGE: KnowledgeItem[] = [
  { id: 1, category: "hot_content", title: "对比实测类爆款结构：钩子-冲突-反转-结论", tag: "数码", metric: "复用 14 次 · 平均完播 41%" },
  { id: 2, category: "hot_content", title: "Before/After 改造类，前 3 秒必出对比", tag: "家居", metric: "复用 9 次 · 平均完播 38%" },
  { id: 3, category: "user_persona", title: "25-32 岁一线城市理性消费男性", tag: "数码", metric: "覆盖 3 个账号" },
  { id: 4, category: "user_persona", title: "敏感肌成分党女性，重测评轻情绪", tag: "美妆", metric: "覆盖 2 个账号" },
  { id: 5, category: "prompt_library", title: "硬核测评分镜提示词模板 v4", tag: "美术", metric: "评分 4.6 / 5" },
  { id: 6, category: "prompt_library", title: "生活方式治愈系画面提示词", tag: "美术", metric: "评分 4.3 / 5" },
  { id: 7, category: "script_library", title: "差评安抚话术：先共情再给替代方案", tag: "客服", metric: "好评率 +12%" },
  { id: 8, category: "script_library", title: "私域导流合规话术（不违规不硬广）", tag: "客服", metric: "转化 8.4%" },
];

// —— Agent 模型配置 ——

export interface AgentConfig {
  code: string;
  name: string;
  primary: string;
  fallback: string;
  calls7d: number;
  cost7d: number;
}
export const AGENT_CONFIGS: AgentConfig[] = [
  { code: "01-positioning", name: "账号定位专家", primary: "deepseek-chat", fallback: "—", calls7d: 142, cost7d: 0.86 },
  { code: "02-content", name: "编导文案专家", primary: "deepseek-chat", fallback: "deepseek-reasoner", calls7d: 980, cost7d: 6.42 },
  { code: "03-art", name: "美术指导专家", primary: "deepseek-chat", fallback: "—", calls7d: 760, cost7d: 4.18 },
  { code: "04-video", name: "视频创作专家", primary: "deepseek-chat", fallback: "—", calls7d: 320, cost7d: 2.04 },
  { code: "05-editing", name: "剪辑专家", primary: "deepseek-chat", fallback: "—", calls7d: 540, cost7d: 3.11 },
  { code: "06-operation", name: "账号运营专家", primary: "deepseek-reasoner", fallback: "deepseek-chat", calls7d: 410, cost7d: 5.77 },
  { code: "07-ads", name: "投流专家", primary: "deepseek-reasoner", fallback: "—", calls7d: 96, cost7d: 1.32 },
  { code: "08-service", name: "客服专家", primary: "deepseek-chat", fallback: "—", calls7d: 1240, cost7d: 7.05 },
];

// —— 通用 KPI ——

export const KPI = {
  activeContent: 37,
  pendingGates: 4,
  publishedToday: 18,
  cost7d: 30.75,
  accounts: 1042,
  avgCompletion: 34.2,
  followersNet7d: 18420,
  roi: 2.4,
};
