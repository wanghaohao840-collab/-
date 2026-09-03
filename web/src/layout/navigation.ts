export type NavigationItem = {
  path: string;
  label: string;
  mobileLabel: string;
  heading: string;
};

export const navigationItems = [
  {
    path: "/overview",
    label: "概览",
    mobileLabel: "概览",
    heading: "学习概览",
  },
  {
    path: "/documents",
    label: "文档库",
    mobileLabel: "文档",
    heading: "文档库",
  },
  {
    path: "/qa",
    label: "智能问答",
    mobileLabel: "问答",
    heading: "智能问答",
  },
  {
    path: "/search",
    label: "文献检索",
    mobileLabel: "检索",
    heading: "文献检索",
  },
  {
    path: "/notes",
    label: "学习笔记",
    mobileLabel: "笔记",
    heading: "学习笔记",
  },
  {
    path: "/insights",
    label: "学习洞察",
    mobileLabel: "洞察",
    heading: "学习洞察",
  },
] as const satisfies readonly NavigationItem[];
