import dayjs from "dayjs";

// 复盘看板用数据。用确定性公式生成自然形态的序列，保证演示稳定。

export const TREND_DAYS = Array.from({ length: 30 }, (_, i) =>
  dayjs().subtract(29 - i, "day").format("M/D"),
);

export const TREND_PLAY = TREND_DAYS.map((_, i) =>
  Math.round(48000 + 9000 * Math.sin(i / 4) + 620 * i + (i % 7 < 2 ? -7000 : 3800)),
);
export const TREND_EXPOSURE = TREND_PLAY.map((v, i) =>
  Math.round(v * (3.0 + 0.3 * Math.sin(i / 3))),
);

export const WEEK_LABELS = Array.from({ length: 8 }, (_, i) => `第${i + 1}周`);
export const COMPLETION_RATE = [27.5, 28.9, 30.1, 29.4, 31.8, 33.2, 34.6, 35.9];
export const INTERACTION_RATE = [4.1, 4.4, 4.2, 4.8, 5.1, 5.0, 5.6, 5.9];
export const COMPLETION_THRESHOLD = 30;

export const RANK_TOP = [
  { name: "百元机皇深度长测", value: 42.1 },
  { name: "618 新品开箱", value: 39.8 },
  { name: "租房改造 Before/After", value: 38.4 },
];
export const RANK_BOTTOM = [
  { name: "咖啡机盲测对比", value: 21.3 },
  { name: "桌搭清单升级", value: 19.7 },
  { name: "周末徒步路线", value: 18.2 },
];

// 7 天 × 24 小时 发布时段效果热力（[hour, day, value]）
export const HEATMAP: [number, number, number][] = [];
for (let d = 0; d < 7; d++) {
  for (let h = 0; h < 24; h++) {
    const base =
      Math.max(0, Math.sin((h - 6) / 3.2)) * 60 +
      (h >= 19 && h <= 22 ? 35 : 0) +
      (h >= 11 && h <= 13 ? 22 : 0) +
      (d >= 5 ? 14 : 0);
    HEATMAP.push([h, d, Math.round(base + (h % 5) * 2)]);
  }
}
export const HEATMAP_DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export const PLATFORM_RADAR = {
  indicators: [
    { name: "完播率", max: 50 },
    { name: "点赞率", max: 12 },
    { name: "评论率", max: 6 },
    { name: "转发率", max: 5 },
    { name: "涨粉率", max: 8 },
  ],
  series: [
    { name: "抖音", value: [36, 8.2, 3.1, 2.4, 5.6] },
    { name: "小红书", value: [31, 9.6, 4.2, 1.8, 4.1] },
    { name: "视频号", value: [28, 6.4, 2.2, 3.6, 3.2] },
  ],
};

export const ROI_DAYS = Array.from({ length: 14 }, (_, i) =>
  dayjs().subtract(13 - i, "day").format("M/D"),
);
export const ROI_SERIES = ROI_DAYS.map((_, i) =>
  Number((1.9 + 0.5 * Math.sin(i / 2.5) + 0.04 * i).toFixed(2)),
);

export const SENTIMENT = [
  { name: "正面", value: 68 },
  { name: "中性", value: 22 },
  { name: "负面", value: 10 },
];

export const FUNNEL = [
  { name: "曝光", value: 100 },
  { name: "互动", value: 46 },
  { name: "高意向", value: 18 },
  { name: "进群/加微", value: 7 },
];
