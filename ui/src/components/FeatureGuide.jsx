import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ChartLine, Gear, ChatCircleDots, Chats, Star, Eye, Newspaper, Scroll,
  X, ArrowLeft, ArrowRight, Sparkle, CircleDashed, ShieldCheck, Phone
} from '@phosphor-icons/react'

/* ───────────────────────────────────────────────
   Data: guide steps (dynamically filtered by restrictedEnabled)
   features: function that receives restrictedEnabled → string[]
   ─────────────────────────────────────────────── */

const GUIDE_STEPS_DEF = [
  {
    tabId: 'welcome',
    cardPos: { top: '50%', left: '260px', transform: 'translateY(-50%)' },
    icon: Sparkle,
    title: '欢迎来到摘星',
    desc: '你的本地智能助手。接下来逐个板块参观，每步会切换到对应页面并高亮重点功能。',
    features: () => [],
    highlights: [],
    drawer: null,
  },
  {
    tabId: 'dashboard',
    cardPos: { bottom: '40px', left: '260px', transform: 'none' },
    icon: ChartLine,
    title: '运行状态仪表盘',
    desc: 'Bot 的控制中枢。启停、统计、延迟，一目了然。',
    features: () => ['启停 Bot', '实时消息统计', '系统健康检测'],
    highlights: ['hl-start', 'hl-msg', 'hl-latency', 'hl-health-db', 'hl-health-wx', 'hl-health-ai'],
    drawer: null,
  },
  {
    tabId: 'config',
    cardPos: { bottom: '40px', right: '40px', transform: 'none' },
    icon: Gear,
    title: '系统配置中心',
    desc: '绑定微信账号是一切功能的前提 — AI 后端、调试台均可在此配置。',
    features: () => ['微信账号绑定', 'AI 服务商切换', 'AI 调试台'],
    highlights: ['hl-wechat-bind', 'hl-ai', 'hl-sandbox'],
    drawer: null,
  },
  {
    tabId: 'push',
    navTab: 'config',
    subHighlight: 'config-push',
    cardPos: { bottom: '40px', right: '40px', transform: 'none' },
    icon: Phone,
    title: '消息推送',
    desc: '扫码绑定推送通道，摘要、提醒、提醒直接推到微信聊天。右侧手机屏展示推送效果。',
    features: () => ['扫码绑定推送', '关键词提醒卡片', '群聊摘要卡片'],
    highlights: ['hl-push-bind', 'hl-push-alert-card', 'hl-push-digest-card'],
    drawer: null,
  },
  {
    tabId: 'assistant',
    cardPos: { bottom: '40px', left: '260px', transform: 'none' },
    icon: ChatCircleDots,
    title: '群聊智能助手',
    desc: '关键词关注提醒 + 定时 AI 摘要 + 公众号即时提醒。每个群独立配置，可推送通知。',
    features: () => ['关键词提醒', '定时 AI 摘要', '消息推送'],
    highlights: ['hl-kw', 'hl-digest'],
    drawer: null,
  },
  {
    tabId: 'chats',
    cardPos: { bottom: '40px', left: '260px', transform: 'none' },
    icon: Chats,
    title: '会话管理',
    desc: '浏览聊天记录。重点介绍 AI 对话功能 - 对任意聊天发起 AI 会话，用聊天记录作为上下文智能问答。',
    features: (r) => ['AI 对话查询聊天', ...(r ? ['撤回拦截'] : []), '聊天导出归档', '全类型消息'],
    highlights: ['hl-ai-chat-btn', 'hl-export'],
    drawer: 'chat',
    drawerHighlight: 'hl-ai-chat-btn',
  },
  {
    tabId: 'favorites',
    cardPos: { bottom: '40px', left: '260px', transform: 'none' },
    icon: Star,
    title: '收藏助手',
    desc: '按类型/标签/关键词筛选收藏。重点：AI 对话功能 - 用收藏内容作为上下文，智能问答你的收藏。',
    features: () => ['筛选条件导出', '收藏 AI 对话', '聊天记录卡片还原'],
    highlights: ['hl-fav-ai', 'hl-type', 'hl-fav-export'],
    drawer: 'fav',
    drawerHighlight: 'hl-fav-ai',
  },
  {
    tabId: 'moments',
    cardPos: { bottom: '40px', left: '260px', transform: 'none' },
    icon: Eye,
    title: '朋友圈助手',
    desc: '浏览动态、图片灯箱、视频下载。重点：AI 对话功能 — 用朋友圈内容作为上下文，智能分析动态趋势。',
    features: (r) => ['朋友圈 AI 对话', ...(r ? ['内容归档'] : []), '联系人筛选 + 图片灯箱'],
    highlights: ['hl-moments-ai', 'hl-contact', 'hl-post'],
    drawer: 'moments',
    drawerHighlight: 'hl-moments-ai',
  },
  {
    tabId: 'oa',
    cardPos: { bottom: '40px', right: '40px', transform: 'none' },
    icon: Newspaper,
    title: '公众号助手',
    desc: '分组 + 定时 AI 摘要 + 即时提醒。多种领域模板，一键简报。',
    features: () => ['公众号分组', '多种摘要模板', '即时提醒'],
    highlights: ['hl-group', 'hl-template', 'hl-oa-monitor'],
    drawer: null,
  },
  {
    tabId: 'logs',
    cardPos: { bottom: '40px', left: '260px', transform: 'none' },
    icon: Scroll,
    title: '运行日志',
    desc: '实时日志流、AI 交互记录、异常快速定位。',
    features: () => ['实时日志流', 'AI 交互记录', '异常定位'],
    highlights: ['hl-log-ai', 'hl-log-digest'],
    drawer: null,
  },
]

/* ───────────────────────────────────────────────
   Mock page content (1:1 from demo-guide.html PAGES)
   ─────────────────────────────────────────────── */

function WelcomePage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards]">
      <div className="bg-bg-card border border-border-main rounded-2xl p-16 text-center">
        <div className="w-16 h-16 rounded-full bg-brand-green-light border-2 border-brand-green/20 flex items-center justify-center mx-auto mb-6 text-[28px] font-bold text-brand-green">W</div>
        <h2 className="text-xl font-bold mb-2">微信助手</h2>
        <p className="text-sm text-text-muted max-w-md mx-auto leading-relaxed">
          你的本地数据管家<br />本地数据 · AI 智能增强 · 一站式内容管理
        </p>
        <div className="flex gap-2 justify-center mt-6">
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">本地优先</span>
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">零云端依赖</span>
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">全类型支持</span>
        </div>
      </div>
    </div>
  )
}

function DashboardPage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards]">
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center justify-between mb-5">
          <div>
            <div className="text-sm font-semibold">Bot 运行状态</div>
            <div className="text-[11px] text-text-muted font-mono">deepseek-v4-flash</div>
          </div>
          <div id="hl-start" className="px-5 py-2 rounded-full text-[13px] font-semibold cursor-pointer bg-brand-green-hover text-white border-none">启停控制</div>
        </div>
        <div className="grid grid-cols-3 gap-4 mt-4">
          <div id="hl-msg" className="bg-bg-raised border border-border-main rounded-2xl p-4">
            <div className="text-[11px] text-text-muted font-semibold">消息处理量</div>
            <div className="text-2xl font-bold font-mono">1,247</div>
            <div className="text-[11px] text-brand-green font-medium">+87 今日</div>
          </div>
          <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
            <div className="text-[11px] text-text-muted font-semibold">运行时长</div>
            <div className="text-2xl font-bold font-mono">2h 15m</div>
            <div className="text-[11px] text-brand-green font-medium">稳定运行</div>
          </div>
          <div id="hl-latency" className="bg-bg-raised border border-border-main rounded-2xl p-4">
            <div className="text-[11px] text-text-muted font-semibold">API 响应</div>
            <div className="text-2xl font-bold font-mono">1.2s</div>
            <div className="text-[11px] text-brand-green font-medium">正常延迟</div>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-5 gap-4 mt-4">
        <div id="hl-health-db" className="bg-bg-raised border border-border-main rounded-2xl p-4">
          <div className="text-[11px] text-text-muted font-semibold">数据库</div>
          <div className="text-sm font-bold text-brand-green">✓ 已连接</div>
        </div>
        <div id="hl-health-wx" className="bg-bg-raised border border-border-main rounded-2xl p-4">
          <div className="text-[11px] text-text-muted font-semibold">微信</div>
          <div className="text-sm font-bold text-brand-green">✓ 在线</div>
        </div>
        <div id="hl-health-ai" className="bg-bg-raised border border-border-main rounded-2xl p-4">
          <div className="text-[11px] text-text-muted font-semibold">AI 后端</div>
          <div className="text-sm font-bold text-brand-green">✓ 连通</div>
        </div>
        <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
          <div className="text-[11px] text-text-muted font-semibold">语义搜索</div>
          <div className="text-sm font-bold text-amber-500 flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
            按需安装
          </div>
        </div>
        <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
          <div className="text-[11px] text-text-muted font-semibold">助手服务</div>
          <div className="text-sm font-bold text-brand-green">✓ 运行</div>
        </div>
      </div>
    </div>
  )
}

function ConfigPage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards] space-y-4">
      <div id="hl-wechat-bind" className="bg-bg-card border border-brand-green/20 rounded-2xl p-6 relative overflow-hidden">
        <div className="absolute top-0 right-0 w-32 h-32 bg-brand-green/5 rounded-full -translate-y-1/2 translate-x-1/4 pointer-events-none" />
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold">微信账号绑定</div>
          <span className="ml-auto text-[10px] font-semibold px-2 py-0.5 rounded-full bg-brand-green-light text-brand-green border border-brand-green/20">必选</span>
        </div>
        <div className="p-4 bg-bg-raised border border-border-main rounded-xl">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 rounded-full bg-brand-green-light flex items-center justify-center text-lg font-bold text-brand-green">W</div>
            <div>
              <div className="text-xs font-medium text-text-main">当前微信账号</div>
              <div className="text-[11px] text-text-muted font-mono">wxid_abc123</div>
            </div>
            <span className="ml-auto inline-flex items-center gap-1.5 text-[10px] font-semibold text-brand-green">
              <span className="w-1.5 h-1.5 rounded-full bg-brand-green animate-pulse" />
              已连接
            </span>
          </div>
          <div className="text-[11px] text-text-muted leading-relaxed">
            绑定微信后才能读取聊天记录、收藏、朋友圈等数据。如显示「已连接」则一切正常。
          </div>
        </div>
      </div>
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-status-info" />
          <div className="text-sm font-semibold">AI 后端配置</div>
        </div>
        <div className="grid gap-4">
          <div id="hl-ai" className="p-4 bg-bg-raised border border-border-main rounded-xl">
            <div className="text-[11px] text-text-muted font-semibold mb-2">AI 服务商</div>
            <div className="px-4 py-2.5 bg-bg-main border border-border-main rounded-full text-[13px] font-mono text-text-muted">deepseek · DeepSeek · 推荐</div>
          </div>
        </div>
      </div>
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-status-info" />
          <div className="text-sm font-semibold">AI 调试台</div>
        </div>
        <div id="hl-sandbox" className="p-4 bg-bg-raised border border-border-main rounded-xl space-y-1.5">
          <div className="flex justify-end"><span className="text-[11px] px-2.5 py-1 rounded-xl bg-brand-green/12 text-text-main max-w-[80%]">你好</span></div>
          <div className="text-[11px] text-text-muted">发送消息测试 AI 连通性</div>
        </div>
      </div>
    </div>
  )
}

function AssistantPage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards] space-y-4">
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold">关键词提醒</div>
        </div>
        <div id="hl-kw" className="p-4 bg-bg-raised border border-border-main rounded-xl">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[13px] font-semibold">技术交流群</span>
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">部署</span>
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">提醒</span>
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">宕机</span>
          </div>
          <div className="text-[11px] text-text-muted">匹配关键词 → 生成提醒通知 → 外接自动化</div>
        </div>
      </div>
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold">定时群聊摘要</div>
        </div>
        <div id="hl-digest" className="p-4 bg-bg-raised border border-border-main rounded-xl">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[13px] font-semibold">产品讨论群</span>
            <div className="flex gap-1">
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">每日 09:00</span>
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">偏行动项</span>
            </div>
          </div>
          <div className="text-[11px] text-text-muted">回溯 12h 未读 · AI 生成摘要 · 可推送通知</div>
        </div>
      </div>
    </div>
  )
}

function ChatsPage({ restrictedEnabled }) {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards] flex gap-4">
      <div className="w-60">
        <div className="bg-bg-card border border-border-main rounded-2xl p-3">
          <div id="hl-search" className="px-3 py-2 bg-bg-raised border border-border-main rounded-full text-xs text-text-muted mb-3">搜索会话...</div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2.5 p-2 bg-bg-raised rounded-[10px]">
              <div className="w-8 h-8 rounded-full bg-brand-green-light" />
              <div>
                <div className="h-2 w-[120px] rounded bg-border-strong mb-1" />
                <div className="h-1.5 w-[60px] rounded bg-border-strong" />
              </div>
            </div>
            <div className="flex items-center gap-2.5 p-2 bg-bg-raised rounded-[10px]">
              <div className="w-8 h-8 rounded-full bg-status-info-soft" />
              <div>
                <div className="h-2 w-[120px] rounded bg-border-strong mb-1" />
                <div className="h-1.5 w-[60px] rounded bg-border-strong" />
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="flex-1">
        <div className="bg-bg-card border border-border-main rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm font-semibold">张三</div>
            <div className="flex gap-2">
              {restrictedEnabled && (
                <div id="hl-anti-revoke" className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-full text-[11px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20 cursor-pointer">
                  <ShieldCheck size={12} weight="fill" /> 撤回拦截
                </div>
              )}
              <div id="hl-ai-chat-btn" className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-full text-[11px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20 cursor-pointer">AI 对话</div>
              <div id="hl-export" className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-full text-[11px] font-medium bg-bg-raised text-text-muted border border-border-main cursor-pointer">导出</div>
            </div>
          </div>
          <div className="flex flex-col gap-2.5 opacity-70">
            <div className="self-end px-3.5 py-2 bg-brand-green/12 rounded-xl rounded-br-sm text-xs max-w-[70%]">明天下午三点开会讨论方案</div>
            <div className="self-start px-3.5 py-2 bg-bg-raised rounded-xl rounded-bl-sm text-xs max-w-[70%]">收到，我准备一下PPT</div>
            <div className="self-end px-3.5 py-2 bg-brand-green/12 rounded-xl rounded-br-sm text-xs max-w-[70%]">重点放在Q2数据上</div>
          </div>
        </div>
      </div>
    </div>
  )
}

function FavoritesPage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards]">
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold">微信收藏</div>
          <div className="ml-auto flex gap-1.5">
            <div id="hl-type" className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium bg-bg-raised text-text-muted border border-border-main cursor-pointer">类型</div>
            <div id="hl-tag" className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium bg-bg-raised text-text-muted border border-border-main cursor-pointer">标签</div>
            <div id="hl-fav-ai" className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-full text-[11px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20 cursor-pointer">AI 对话</div>
          </div>
        </div>
        <div className="flex flex-col gap-2">
          <div id="hl-fav-card" className="p-4 bg-bg-raised border border-border-main rounded-xl flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-brand-green-light flex items-center justify-center text-xs text-brand-green font-semibold">T</div>
            <div className="flex-1">
              <div className="text-xs mb-0.5">项目排期表 v2</div>
              <div className="text-[10px] text-text-muted">工作 · 2024-06-15</div>
            </div>
            <div id="hl-fav-export" className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-medium bg-bg-raised text-text-muted border border-border-main">导出</div>
          </div>
          <div className="p-4 bg-bg-raised border border-border-main rounded-xl flex items-center gap-2.5 opacity-50">
            <div className="w-7 h-7 rounded-lg bg-status-info-soft flex items-center justify-center text-xs text-status-info font-semibold">L</div>
            <div className="flex-1">
              <div className="text-xs mb-0.5">深度学习论文合集</div>
              <div className="text-[10px] text-text-muted">技术 · 2024-06-12</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function MomentsPage({ restrictedEnabled }) {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards]">
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold">朋友圈动态</div>
          <div className="ml-auto flex gap-1.5">
            <div id="hl-contact" className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium bg-bg-raised text-text-muted border border-border-main cursor-pointer">联系人</div>
            {restrictedEnabled && (
              <div id="hl-protect" className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20 cursor-pointer">
                <ShieldCheck size={10} weight="fill" /> 内容归档
              </div>
            )}
            <div id="hl-moments-ai" className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-full text-[11px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20 cursor-pointer">AI 对话</div>
          </div>
        </div>
        <div id="hl-post" className="p-4 bg-bg-raised border border-border-main rounded-xl">
          <div className="flex items-center gap-2.5 mb-3">
            <div className="w-8 h-8 rounded-full bg-brand-green-light" />
            <div>
              <div className="text-xs font-semibold mb-0.5">李明</div>
              <div className="text-[10px] text-text-muted">3 小时前</div>
            </div>
          </div>
          <div className="text-xs mb-2.5">周末去了趟西湖，风景太美了</div>
          <div className="flex gap-1.5 mb-3">
            <img src="https://picsum.photos/seed/westlake1/200/200" className="w-[72px] h-[72px] rounded-lg object-cover" alt="" />
            <img src="https://picsum.photos/seed/westlake2/200/200" className="w-[72px] h-[72px] rounded-lg object-cover" alt="" />
            <img src="https://picsum.photos/seed/westlake3/200/200" className="w-[72px] h-[72px] rounded-lg object-cover" alt="" />
            <img src="https://picsum.photos/seed/sunset88/200/200" className="w-[72px] h-[72px] rounded-lg object-cover" alt="" />
            <img src="https://picsum.photos/seed/lotus7/200/200" className="w-[72px] h-[72px] rounded-lg object-cover" alt="" />
            <img src="https://picsum.photos/seed/bridge44/200/200" className="w-[72px] h-[72px] rounded-lg object-cover" alt="" />
          </div>
          <div className="flex items-center gap-3 text-[11px] text-text-muted">
            <span>3 赞</span><span>2 评论</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function PushPage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards] flex gap-8 items-center min-h-[540px] py-5 px-16 justify-start">
      {/* Left status rail */}
      <div className="flex flex-col gap-3.5 w-[200px] shrink-0">
        <div id="hl-push-bind" className="bg-bg-card border border-border-main rounded-[14px] p-3.5">
          <div className="text-[13px] font-bold mb-2.5 flex items-center gap-1.5">
            <svg width="14" height="14" viewBox="0 0 256 256" fill="none"><path d="M40 48h176v128H80l-40 40V48z" stroke="currentColor" strokeWidth="16" strokeLinejoin="round" /></svg>
            iLink 推送绑定
          </div>
          <div className="flex items-center gap-2 p-2 bg-bg-raised rounded-lg text-xs">
            <span className="w-2 h-2 rounded-full bg-brand-green shrink-0 relative">
              <span className="absolute inset-0 rounded-full bg-brand-green animate-[ping_1.5s_cubic-bezier(0,0,0.2,1)_infinite] opacity-60" />
            </span>
            <div className="flex-1">
              <div className="font-semibold text-text-main">已绑定</div>
              <div className="text-[10px] text-text-muted font-mono">微信ClawBot · 2h ago</div>
            </div>
            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[9px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">活跃</span>
          </div>
          <button className="w-full mt-2.5 py-[7px] bg-transparent border border-border-main rounded-lg text-[11px] text-text-muted font-inherit cursor-pointer">解绑</button>
        </div>
        <div id="hl-push-history" className="bg-bg-card border border-border-main rounded-[14px] p-3.5">
          <div className="flex items-center gap-2 mb-2.5">
            <div className="w-1 h-3.5 rounded-sm bg-status-info" />
            <div className="text-xs font-semibold">最近推送</div>
          </div>
          <div className="flex flex-col gap-2.5">
            <div className="flex gap-2.5 p-2 rounded-lg bg-bg-raised border border-transparent hover:border-border-main transition-colors">
              <div className="w-2 h-2 rounded-full bg-[#e53935] mt-1.5 shrink-0" />
              <div className="text-[11px] text-text-muted leading-relaxed flex-1"><b className="text-text-main font-semibold block mb-0.5 text-xs">关键词提醒</b>项目核心群 · 15:42</div>
            </div>
            <div className="flex gap-2.5 p-2 rounded-lg bg-bg-raised border border-transparent hover:border-border-main transition-colors">
              <div className="w-2 h-2 rounded-full bg-[#07c160] mt-1.5 shrink-0" />
              <div className="text-[11px] text-text-muted leading-relaxed flex-1"><b className="text-text-main font-semibold block mb-0.5 text-xs">群聊摘要</b>产品讨论群 · 09:00</div>
            </div>
            <div className="flex gap-2.5 p-2 rounded-lg bg-bg-raised border border-transparent hover:border-border-main transition-colors">
              <div className="w-2 h-2 rounded-full bg-[#3772cf] mt-1.5 shrink-0" />
              <div className="text-[11px] text-text-muted leading-relaxed flex-1"><b className="text-text-main font-semibold block mb-0.5 text-xs">公众号提醒</b>机器之心 · 08:15</div>
            </div>
          </div>
        </div>
      </div>

      {/* Phone frame — light mode */}
      <div className="w-[280px] h-[500px] bg-[#ededed] rounded-[36px] relative flex flex-col overflow-hidden border-[4px] border-[#1a1a1a] shrink-0" style={{ boxShadow: '0 20px 50px -8px rgba(0,0,0,0.3), 0 0 0 1px rgba(0,0,0,0.08)' }}>
        <div className="absolute top-2 left-1/2 -translate-x-1/2 w-[90px] h-[24px] bg-black rounded-[14px] z-[100]" />
        <div className="h-10 px-6 pt-3 flex justify-between text-[#1a1a1a] text-[13px] font-semibold shrink-0 z-50">
          <span>15:42</span><span className="text-xs">5G 88</span>
        </div>
        <div className="h-[44px] flex items-center justify-between px-3 border-b border-black/[0.08] shrink-0 bg-[#ededed]">
          <span className="text-[22px] text-black font-light leading-none">‹</span>
          <div className="text-[15px] font-semibold text-[#1a1a1a] flex items-center gap-1 tracking-[0.3px]">微信ClawBot <div className="bg-[#d5d5d5] text-[#555] text-[10px] font-bold py-0.5 px-1.5 rounded">AI</div></div>
          <div className="flex gap-1 items-center px-1 py-2.5"><div className="w-[5px] h-[5px] rounded-full bg-black" /><div className="w-[5px] h-[5px] rounded-full bg-black" /><div className="w-[5px] h-[5px] rounded-full bg-black" /></div>
        </div>
        <div className="flex-1 overflow-y-auto p-[14px_12px] flex flex-col gap-3 bg-[#ededed] text-[14px]" style={{ scrollbarWidth: 'none' }}>
          <div className="text-center text-[11px] text-[#888] my-1 tracking-[0.3px]">下午 15:40</div>
          <div className="flex gap-2.5 items-start">
            <div className="w-[42px] h-[42px] rounded-lg bg-[#ef4545] shrink-0 flex items-center justify-center gap-1 shadow-[0_2px_6px_rgba(239,69,68,0.25)]"><div className="w-[9px] h-[9px] bg-white rounded-full" /><div className="w-[9px] h-[9px] bg-white rounded-full" /></div>
            <div><div className="relative bg-white text-[#1a1a1a] text-[15px] p-[13px_15px] rounded-lg leading-[1.55] max-w-[78%] shadow-[0_1px_2px_rgba(0,0,0,0.06)]">您好，我是摘星，您的微信小助手<br /><br />错过的重要消息、群聊和公众号，就让我来替您盯着</div></div>
          </div>
          <div id="hl-push-alert-card" className="flex gap-2.5 items-start">
            <div className="w-[42px] h-[42px] rounded-lg bg-[#ef4545] shrink-0 flex items-center justify-center gap-1 shadow-[0_2px_6px_rgba(239,69,68,0.25)]"><div className="w-[9px] h-[9px] bg-white rounded-full" /><div className="w-[9px] h-[9px] bg-white rounded-full" /></div>
            <div><div className="relative bg-white border border-black/[0.06] rounded-[10px] max-w-[78%] overflow-hidden shadow-[0_2px_4px_rgba(0,0,0,0.05)]">
              <div className="p-[13px_15px] text-[15px] font-bold border-b border-black/[0.06] flex items-center gap-1.5 text-[#e53935]">
                <svg width="14" height="14" viewBox="0 0 256 256" fill="none"><path d="M56 104a72 72 0 0 1 144 0c0 35 24 60 24 84H32c0-24 24-49 24-84Z" stroke="currentColor" strokeWidth="16" strokeLinejoin="round" /><path d="M96 224h64" stroke="currentColor" strokeWidth="16" strokeLinecap="round" /></svg>
                关键词匹配提醒
              </div>
              <div className="p-[13px_15px] text-sm text-[#333] flex flex-col gap-2">
                <div className="flex items-start text-sm text-[#333] gap-2"><div className="text-[#888] w-[42px] shrink-0">群聊</div>项目核心群</div>
                <div className="flex items-start text-sm text-[#333] gap-2"><div className="text-[#888] w-[42px] shrink-0">发送</div>王总</div>
                <div className="flex items-center text-sm text-[#333] gap-2"><div className="text-[#888] w-[42px] shrink-0">命中</div><div className="flex gap-1.5"><span className="bg-[#fff1f0] text-[#f5222d] text-[13px] px-1.5 py-0.5 rounded font-medium">BUG</span><span className="bg-[#fff1f0] text-[#f5222d] text-[13px] px-1.5 py-0.5 rounded font-medium">宕机</span></div></div>
              </div>
              <div className="p-[11px_15px] text-[13px] text-[#333] border-t border-dashed border-black/[0.12] text-[#666] italic">@所有人 紧急修复BUG！服务器宕机</div>
            </div></div>
          </div>
          <div className="text-center text-xs text-[#888] my-1 tracking-[0.3px]">刚刚</div>
          <div id="hl-push-digest-card" className="flex gap-2.5 items-start">
            <div className="w-[42px] h-[42px] rounded-lg bg-[#ef4545] shrink-0 flex items-center justify-center gap-1 shadow-[0_2px_6px_rgba(239,69,68,0.25)]"><div className="w-[9px] h-[9px] bg-white rounded-full" /><div className="w-[9px] h-[9px] bg-white rounded-full" /></div>
            <div><div className="relative bg-white border border-black/[0.06] rounded-[10px] max-w-[78%] overflow-hidden shadow-[0_2px_4px_rgba(0,0,0,0.05)]">
              <div className="p-[13px_15px] text-[15px] font-bold border-b border-black/[0.06] flex items-center gap-1.5 text-[#07c160]">
                <svg width="14" height="14" viewBox="0 0 256 256" fill="none"><path d="M144 64l-32 80-32-32-32 32" stroke="currentColor" strokeWidth="16" strokeLinecap="round" strokeLinejoin="round" /><path d="M176 48l16 16-16 16" stroke="currentColor" strokeWidth="16" strokeLinecap="round" strokeLinejoin="round" /></svg>
                智能群聊摘要
              </div>
              <div className="p-[13px_15px] text-sm text-[#333]">
                <div className="font-bold text-[14px] mb-2.5 text-[#1a1a1a]">「集团大干100天攻坚群」</div>
                <div className="flex flex-col gap-2 text-[14px] leading-[1.45] text-[#333]">
                  <div><b className="text-[#e53935]">突发调期</b> 客户发布会提前至周五，全员留守</div>
                  <div><b className="text-[#e53935]">系统提醒</b> 生产502，数据库死锁，已暂停合入</div>
                  <div><b className="text-[#e53935]">财务通知</b> 报销系统今晚24点关闭</div>
                </div>
              </div>
              <div className="p-[11px_15px] text-[13px] text-[#576b95] border-t border-dashed border-black/[0.12] text-center font-medium">已折叠 427 条闲聊</div>
            </div></div>
          </div>
        </div>
        <div className="h-[52px] bg-[#f7f7f7] border-t border-black/[0.06] flex items-center px-3 gap-2 shrink-0">
          <svg viewBox="0 0 24 24" width="26" height="26" stroke="#1a1a1a" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" className="shrink-0"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" /><path d="M15.54 8.46a5 5 0 0 1 0 7.07" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" /></svg>
          <div className="flex-1 h-10 bg-white rounded-md flex items-center px-3 text-[#1a1a1a] text-[15px] border border-black/[0.08]" />
          <svg viewBox="0 0 256 256" width="24" height="24" fill="none" stroke="#1a1a1a" strokeWidth="16" strokeLinecap="round" strokeLinejoin="round" className="shrink-0"><circle cx="128" cy="128" r="40" /><path d="M128 80v-8M128 184v-8M80 128h-8M184 128h-8" /></svg>
          <div className="w-[30px] h-[30px] rounded-full border-[1.5px] border-[#1a1a1a] flex items-center justify-center text-[#1a1a1a] font-bold text-base shrink-0">＋</div>
        </div>
        <div className="h-5 bg-[#f7f7f7] flex justify-center items-end pb-1 shrink-0">
          <div className="w-[110px] h-[4px] bg-black rounded-[100px]" />
        </div>
      </div>
    </div>
  )
}

function OAPage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards] space-y-4">
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold">公众号即时提醒</div>
        </div>
        <div id="hl-oa-monitor" className="p-4 bg-bg-raised border border-border-main rounded-xl">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[13px] font-semibold">科技资讯</span>
            <div className="flex gap-1">
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">3 个号</span>
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">消息推送</span>
            </div>
          </div>
          <div className="text-[11px] text-text-muted">公众号发新文 → 即时推送通知 → 不漏重要更新</div>
        </div>
      </div>
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold">公众号分组摘要</div>
        </div>
        <div id="hl-group" className="p-4 bg-bg-raised border border-border-main rounded-xl">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[13px] font-semibold">科技资讯</span>
            <div className="flex gap-1">
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">科技详报</span>
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">每日 09:00</span>
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">6 个号</span>
            </div>
          </div>
          <div className="text-[11px] text-text-muted">智能回溯 · AI 生成摘要 · 可推送通知</div>
        </div>
      </div>
      <div className="bg-bg-card border border-border-main rounded-2xl p-6">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-status-info" />
          <div className="text-sm font-semibold">摘要模板</div>
        </div>
        <div id="hl-template" className="flex gap-1.5 flex-wrap">
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">默认</span>
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-brand-green-light text-brand-green border border-brand-green/20">科技详报</span>
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium bg-bg-raised text-text-muted border border-border-main">娱乐简报</span>
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium bg-bg-raised text-text-muted border border-border-main">商业</span>
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium bg-bg-raised text-text-muted border border-border-main">新闻</span>
          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-medium bg-bg-raised text-text-muted border border-border-main">自定义</span>
        </div>
      </div>
    </div>
  )
}

function LogsPage() {
  return (
    <div className="animate-[pageIn_0.4s_cubic-bezier(0.16,1,0.3,1)_forwards]">
      <div className="bg-bg-card border border-border-main rounded-2xl p-6 font-mono text-[11px] text-text-muted leading-loose">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-1.5 h-5 rounded-sm bg-brand-green" />
          <div className="text-sm font-semibold font-sans">实时日志</div>
        </div>
        <div className="opacity-40">[09:15:02] Bot started · database backend</div>
        <div className="text-brand-green">[09:15:03] Database connected ✓</div>
        <div id="hl-log-ai" className="text-brand-green">[09:16:13] AI response: 1.2s · 247 tokens</div>
        <div id="hl-log-digest" className="text-brand-green">[09:17:46] Digest generated: 3 key points</div>
      </div>
    </div>
  )
}

/* Map tabId to mock page component (pages that need restrictedEnabled use wrapper) */
function getMockPage(tabId, restrictedEnabled) {
  const pages = {
    welcome: WelcomePage,
    dashboard: DashboardPage,
    config: ConfigPage,
    push: PushPage,
    assistant: AssistantPage,
    chats: () => <ChatsPage restrictedEnabled={restrictedEnabled} />,
    favorites: FavoritesPage,
    moments: () => <MomentsPage restrictedEnabled={restrictedEnabled} />,
    oa: OAPage,
    logs: LogsPage,
  }
  return pages[tabId] || DashboardPage
}

const TAB_LABELS = {
  welcome: '欢迎', dashboard: '运行状态', config: '系统配置', push: '消息推送', assistant: '群聊助手',
  chats: '会话管理', favorites: '收藏助手', moments: '朋友圈助手', oa: '公众号助手', logs: '运行日志',
}

/* ───────────────────────────────────────────────
   AI Chat Drawer (1:1 from demo-guide.html)
   ─────────────────────────────────────────────── */

function AIDrawer({ type, onClose }) {
  const isChat = type === 'chat'
  const isMoments = type === 'moments'
  const title = isChat ? 'AI 对话 · 张三' : isMoments ? 'AI 对话 · 朋友圈动态' : 'AI 对话 · 收藏内容'
  const ctxCount = isChat ? '312' : isMoments ? '86' : '47'
  const ctxLabel = isChat ? '聊天记录' : isMoments ? '朋友圈动态' : '收藏内容'
  const tokenPct = isChat ? '6.4%' : isMoments ? '5.8%' : '4%'
  const tokenLabel = isChat ? '8.2K / 128K tokens' : isMoments ? '7.1K / 128K tokens' : '5.1K / 128K tokens'
  const placeholder = isChat ? '追问关于聊天内容的问题...' : isMoments ? '追问朋友圈动态...' : '追问收藏内容...'

  return (
    <motion.div
      initial={{ x: 420 }}
      animate={{ x: 0 }}
      exit={{ x: 420 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="fixed top-0 right-0 w-[420px] h-screen flex flex-col overflow-hidden z-50"
      style={{
        background: 'var(--drawer-bg, rgba(10,10,10,0.6))',
        WebkitBackdropFilter: 'blur(28px) saturate(180%)',
        backdropFilter: 'blur(28px) saturate(180%)',
        borderLeft: '1px solid var(--drawer-border, rgba(255,255,255,0.08))',
      }}
    >
      {/* Header */}
      <div className="px-5 py-4 border-b border-border-main flex items-center justify-between">
        <div className="text-sm font-semibold">{title}</div>
        <button
          onClick={onClose}
          className="w-7 h-7 rounded-full border border-border-main flex items-center justify-center text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors cursor-pointer"
        >
          <X size={14} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-3">
        <div className="text-[11px] text-text-muted text-center mb-2">
          已加载 {ctxCount} 条{ctxLabel}作为上下文
        </div>

        {isChat ? <ChatDrawerMessages /> : isMoments ? <MomentsDrawerMessages /> : <FavDrawerMessages />}
      </div>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-border-main">
        <div className="flex items-center justify-between text-[10px] text-text-muted font-mono">
          <span>{tokenLabel}</span>
          {isChat && (
            <div className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-status-warn-soft text-status-warn border border-status-warn/20">
              压缩历史
            </div>
          )}
        </div>
        <div className="h-1 bg-bg-inset rounded-sm mt-2">
          <div
            className="h-full rounded-sm bg-brand-green transition-all duration-300"
            style={{ width: tokenPct }}
          />
        </div>
        <div className="flex gap-2 mt-3">
          <input
            className="flex-1 px-4 py-2.5 bg-bg-raised border border-border-main rounded-full text-[13px] text-text-main outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15 font-sans"
            placeholder={placeholder}
            readOnly
          />
          <button className="px-3.5 py-2 rounded-full text-xs font-semibold bg-brand-green-hover text-white cursor-pointer border-none">发送</button>
        </div>
      </div>
    </motion.div>
  )
}

function ChatDrawerMessages() {
  return (
    <>
      <div className="self-end max-w-[85%] px-4 py-3 bg-brand-green/12 rounded-xl rounded-br-sm text-[13px] leading-relaxed">
        最近一周我们讨论了什么重要决定？
      </div>
      <div className="self-start max-w-[85%] px-4 py-3 bg-bg-raised border border-border-main rounded-xl rounded-bl-sm text-[13px] leading-relaxed">
        <div className="flex items-center gap-1.5 mb-2 text-[11px] text-brand-green font-semibold">
          <div className="w-4 h-4 rounded-full bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-[9px] text-brand-green font-bold">W</div>
          AI 助手
        </div>
        根据聊天记录，本周有三个重要决定：<br /><br />
        1. <b className="text-brand-green">Q2 产品方向</b> - 决定重点放在数据分析模块，放弃直播功能<br />
        2. <b className="text-brand-green">团队扩招</b> - 计划下月增加 2 名前端工程师<br />
        3. <b className="text-brand-green">客户交付</b> - 客户A的交付延期到 7 月 15 日
      </div>
      <div className="self-end max-w-[85%] px-4 py-3 bg-brand-green/12 rounded-xl rounded-br-sm text-[13px] leading-relaxed">
        关于客户A延期，张三当时怎么说的？
      </div>
      <div className="self-start max-w-[85%] px-4 py-3 bg-bg-raised border border-border-main rounded-xl rounded-bl-sm text-[13px] leading-relaxed">
        <div className="flex items-center gap-1.5 mb-2 text-[11px] text-brand-green font-semibold">
          <div className="w-4 h-4 rounded-full bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-[9px] text-brand-green font-bold">W</div>
          AI 助手
        </div>
        <div className="flex items-center gap-1 py-1 text-brand-green text-xs">
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite]" />
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite_0.2s]" />
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite_0.4s]" />
          <span className="ml-1">正在分析...</span>
        </div>
      </div>
    </>
  )
}

function FavDrawerMessages() {
  return (
    <>
      <div className="self-end max-w-[85%] px-4 py-3 bg-brand-green/12 rounded-xl rounded-br-sm text-[13px] leading-relaxed">
        我的收藏里有哪些和工作项目相关的？
      </div>
      <div className="self-start max-w-[85%] px-4 py-3 bg-bg-raised border border-border-main rounded-xl rounded-bl-sm text-[13px] leading-relaxed">
        <div className="flex items-center gap-1.5 mb-2 text-[11px] text-brand-green font-semibold">
          <div className="w-4 h-4 rounded-full bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-[9px] text-brand-green font-bold">W</div>
          AI 助手
        </div>
        在你的 47 条收藏中，有 12 条与工作项目相关：<br /><br />
        1. <b className="text-brand-green">项目排期表 v2</b> - 包含 Q2 各模块时间节点<br />
        2. <b className="text-brand-green">客户需求文档</b> - 客户A的功能需求清单<br />
        3. <b className="text-brand-green">技术方案评审</b> - 数据分析模块架构设计<br /><br />
        其余 9 条分布在会议纪要、设计稿链接和邮件备忘中。
      </div>
      <div className="self-end max-w-[85%] px-4 py-3 bg-brand-green/12 rounded-xl rounded-br-sm text-[13px] leading-relaxed">
        帮我对比一下排期表和客户需求，看看有没有冲突
      </div>
      <div className="self-start max-w-[85%] px-4 py-3 bg-bg-raised border border-border-main rounded-xl rounded-bl-sm text-[13px] leading-relaxed">
        <div className="flex items-center gap-1.5 mb-2 text-[11px] text-brand-green font-semibold">
          <div className="w-4 h-4 rounded-full bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-[9px] text-brand-green font-bold">W</div>
          AI 助手
        </div>
        <div className="flex items-center gap-1 py-1 text-brand-green text-xs">
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite]" />
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite_0.2s]" />
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite_0.4s]" />
          <span className="ml-1">正在交叉比对...</span>
        </div>
      </div>
    </>
  )
}

function MomentsDrawerMessages() {
  return (
    <>
      <div className="self-end max-w-[85%] px-4 py-3 bg-brand-green/12 rounded-xl rounded-br-sm text-[13px] leading-relaxed">
        帮我分析一下最近朋友圈的趋势和热门话题
      </div>
      <div className="self-start max-w-[85%] px-4 py-3 bg-bg-raised border border-border-main rounded-xl rounded-bl-sm text-[13px] leading-relaxed">
        <div className="flex items-center gap-1.5 mb-2 text-[11px] text-brand-green font-semibold">
          <div className="w-4 h-4 rounded-full bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-[9px] text-brand-green font-bold">W</div>
          AI 助手
        </div>
        根据最近 86 条朋友圈动态，分析如下：<br /><br />
        <b className="text-brand-green">🔥 热门话题</b><br />
        1. <b>AI 工具分享</b> - 5 位朋友分享了各种 AI 效率工具<br />
        2. <b>周末出游</b> - 3 位朋友发了露营/徒步照片<br />
        3. <b>读书笔记</b> - 2 位朋友推荐了技术书籍<br /><br />
        <b className="text-brand-green">📈 活跃度趋势</b><br />
        本周朋友圈活跃度较上周下降 12%，周四、周五最为活跃。
      </div>
      <div className="self-end max-w-[85%] px-4 py-3 bg-brand-green/12 rounded-xl rounded-br-sm text-[13px] leading-relaxed">
        关于 AI 工具那块，能具体说说有哪些吗？
      </div>
      <div className="self-start max-w-[85%] px-4 py-3 bg-bg-raised border border-border-main rounded-xl rounded-bl-sm text-[13px] leading-relaxed">
        <div className="flex items-center gap-1.5 mb-2 text-[11px] text-brand-green font-semibold">
          <div className="w-4 h-4 rounded-full bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-[9px] text-brand-green font-bold">W</div>
          AI 助手
        </div>
        <div className="flex items-center gap-1 py-1 text-brand-green text-xs">
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite]" />
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite_0.2s]" />
          <span className="w-1 h-1 rounded-full bg-brand-green animate-[typingBlink_1.4s_infinite_0.4s]" />
          <span className="ml-1">正在提取分享内容...</span>
        </div>
      </div>
    </>
  )
}

/* ───────────────────────────────────────────────
   Guide Card
   ─────────────────────────────────────────────── */

function GuideCard({ step, stepIndex, totalSteps, onPrev, onNext, onSkip, onGoToStep }) {
  const isLast = stepIndex === totalSteps - 1
  const isFirst = stepIndex === 0

  return (
    <motion.div
      key={`card-${stepIndex}`}
      initial={{ opacity: 0, y: 20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="w-[340px] rounded-[20px] overflow-hidden absolute pointer-events-auto"
      style={{
        ...step.cardPos,
        background: 'var(--guide-bg, rgba(10,10,10,0.5))',
        WebkitBackdropFilter: 'blur(28px) saturate(180%)',
        backdropFilter: 'blur(28px) saturate(180%)',
        border: '1px solid var(--guide-border, rgba(255,255,255,0.1))',
        boxShadow: 'var(--guide-shadow, 0 24px 80px rgba(0,0,0,0.3), 0 0 0 1px rgba(24,226,153,0.06), inset 0 1px 0 rgba(255,255,255,0.06))',
        transition: 'top 0.5s cubic-bezier(0.16,1,0.3,1), left 0.5s cubic-bezier(0.16,1,0.3,1), bottom 0.5s cubic-bezier(0.16,1,0.3,1), right 0.5s cubic-bezier(0.16,1,0.3,1)',
      }}
    >
      {/* Progress bar */}
      <div className="h-[2px] bg-bg-inset">
        <div
          className="h-full bg-gradient-to-r from-brand-green to-brand-green-hover rounded-sm transition-all duration-400"
          style={{ width: `${((stepIndex + 1) / totalSteps) * 100}%`, transitionTimingFunction: 'cubic-bezier(0.16,1,0.3,1)' }}
        />
      </div>

      {/* Close button */}
      <button
        onClick={onSkip}
        className="absolute top-2.5 right-2.5 w-7 h-7 rounded-full border border-border-main flex items-center justify-center text-text-muted hover:text-text-main hover:bg-bg-raised transition-colors z-10 cursor-pointer bg-transparent"
        title="跳过引导"
      >
        <X size={14} />
      </button>

      {/* Content */}
      <div className="p-6 pb-3">
        {/* Step indicators */}
        <div className="flex items-center gap-1.5 mb-4">
          {Array.from({ length: totalSteps }, (_, i) => (
            <button
              key={i}
              onClick={() => onGoToStep(i)}
              className={`h-1.5 rounded-full transition-all duration-300 cursor-pointer border-none ${
                i === stepIndex
                  ? 'w-[22px] bg-brand-green'
                  : 'w-1.5 bg-border-strong hover:bg-white/25'
              }`}
            />
          ))}
        </div>

        {/* Step content with animation */}
        <motion.div
          key={`step-content-${stepIndex}`}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
        >
          {isFirst && (
            <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-semibold bg-brand-green-light border border-brand-green/20 text-brand-green mb-4">
              <span className="w-1.5 h-1.5 rounded-full bg-brand-green relative">
                <span className="absolute inset-0 rounded-full bg-brand-green animate-[ping_1.5s_cubic-bezier(0,0,0.2,1)_infinite] opacity-60" />
              </span>
              首次启动
            </div>
          )}

          {/* Icon */}
          <div className="w-12 h-12 rounded-[14px] bg-brand-green-light border border-brand-green/15 flex items-center justify-center mb-4">
            <step.icon size={24} weight="regular" className="text-brand-green" />
          </div>

          {/* Title + counter */}
          <div className="flex items-center justify-between">
            <div className="text-base font-semibold text-text-main">{step.title}</div>
            <div className="text-[11px] font-mono text-text-muted font-medium">{stepIndex + 1} / {totalSteps}</div>
          </div>

          {/* Description */}
          <div className="text-[13px] text-text-muted leading-relaxed mt-1.5 mb-4">{step.desc}</div>

          {/* Feature chips */}
          {step.features.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-5">
              {step.features.map((f, i) => {
                const isHot = (step.drawer && i === 0) || (step.tabId === 'config' && i === 0)
                return (
                  <div
                    key={i}
                    className={`inline-flex items-center gap-[5px] px-2.5 py-1 rounded-full text-[11px] font-medium border ${
                      isHot
                        ? 'bg-brand-green-light border-brand-green/20 text-brand-green font-semibold'
                        : 'bg-bg-raised border-border-main text-text-muted'
                    }`}
                  >
                    <span className="w-1 h-1 rounded-full bg-brand-green flex-shrink-0" />
                    {f}
                  </div>
                )
              })}
            </div>
          )}
        </motion.div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2.5 px-6 py-4 border-t border-border-main">
        <button onClick={onSkip} className="bg-transparent text-text-muted text-[11px] py-2 px-0 cursor-pointer border-none hover:text-text-main font-sans">
          跳过
        </button>
        {!isFirst && (
          <button
            onClick={onPrev}
            className="py-2 px-5 rounded-full text-[13px] font-semibold cursor-pointer bg-transparent text-text-muted border border-border-main hover:text-text-main hover:border-white/15 transition-colors font-sans"
          >
            上一步
          </button>
        )}
        <button
          onClick={onNext}
          className="flex-1 py-2 px-5 rounded-full text-[13px] font-semibold cursor-pointer border-none transition-opacity hover:opacity-90 active:scale-[0.97] font-sans"
          style={{
            background: isLast ? 'var(--brand-green)' : 'var(--brand-green-hover)',
            color: isLast ? '#0a0a0a' : 'white',
          }}
        >
          {isLast ? '开始使用' : '下一步'}
        </button>
      </div>
    </motion.div>
  )
}

/* ───────────────────────────────────────────────
   Main FeatureGuide Component
   ─────────────────────────────────────────────── */

export default function FeatureGuide({ onTabChange, onComplete, restrictedEnabled = false }) {
  const [currentStep, setCurrentStep] = useState(0)
  const [drawerType, setDrawerType] = useState(null) // 'chat' | 'fav' | null

  // Resolve GUIDE_STEPS at runtime with restrictedEnabled filtering
  const GUIDE_STEPS = GUIDE_STEPS_DEF.map(s => ({
    ...s,
    features: s.features(restrictedEnabled),
  }))

  const step = GUIDE_STEPS[currentStep]
  const totalSteps = GUIDE_STEPS.length

  // Sync sidebar nav with current guide step
  useEffect(() => {
    // If step has subHighlight (like push), use navTab instead of tabId
    const appTabId = step.subHighlight ? step.navTab : (step.tabId === 'welcome' ? 'dashboard' : step.tabId)
    onTabChange?.(appTabId)

    // Apply nav pulse animation to the active nav item
    const navItems = document.querySelectorAll('nav button')
    navItems.forEach(btn => {
      btn.classList.remove('nav-item-guide-pulse')
    })
    // Small delay to let the nav item become active first
    const timer = setTimeout(() => {
      navItems.forEach(btn => {
        if (btn.textContent?.trim() === TAB_LABELS[step.tabId]) {
          btn.classList.add('nav-item-guide-pulse')
          setTimeout(() => btn.classList.remove('nav-item-guide-pulse'), 2500)
        }
      })
    }, 100)
    return () => clearTimeout(timer)
  }, [currentStep, step.tabId, onTabChange])

  // Apply element highlights with stagger
  useEffect(() => {
    // Clear all previous highlights
    document.querySelectorAll('.guide-highlight').forEach(el => {
      el.classList.remove('guide-highlight', 'guide-highlight-active')
    })

    if (step.highlights.length === 0) return

    const timers = step.highlights.map((id, i) =>
      setTimeout(() => {
        const el = document.getElementById(id)
        if (el) {
          el.classList.add('guide-highlight', 'guide-highlight-active')
          setTimeout(() => el.classList.remove('guide-highlight-active'), 600)
        }
      }, 500 + i * 300)
    )

    return () => timers.forEach(clearTimeout)
  }, [currentStep, step.highlights])

  // Drawer management
  useEffect(() => {
    if (step.drawer) {
      setDrawerType(step.drawer)
    } else {
      setDrawerType(null)
    }
  }, [currentStep, step.drawer])

  // Keyboard navigation
  useEffect(() => {
    function handleKey(e) {
      if (e.key === 'ArrowRight' || e.key === 'Enter') nextStep()
      if (e.key === 'ArrowLeft') prevStep()
      if (e.key === 'Escape') finish()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  })

  // Apply dark/light mode glass variables to guide card and drawer
  useEffect(() => {
    const isDark = document.documentElement.classList.contains('dark')
    const root = document.documentElement
    if (isDark) {
      root.style.setProperty('--guide-bg', 'rgba(10,10,10,0.5)')
      root.style.setProperty('--guide-border', 'rgba(255,255,255,0.1)')
      root.style.setProperty('--guide-shadow', '0 24px 80px rgba(0,0,0,0.3), 0 0 0 1px rgba(24,226,153,0.06), inset 0 1px 0 rgba(255,255,255,0.06)')
      root.style.setProperty('--drawer-bg', 'rgba(10,10,10,0.6)')
      root.style.setProperty('--drawer-border', 'rgba(255,255,255,0.08)')
    } else {
      root.style.setProperty('--guide-bg', 'rgba(255,255,255,0.7)')
      root.style.setProperty('--guide-border', 'rgba(0,0,0,0.15)')
      root.style.setProperty('--guide-shadow', '0 24px 80px rgba(0,0,0,0.12), 0 0 0 1px rgba(24,226,153,0.1), inset 0 1px 0 rgba(255,255,255,0.5)')
      root.style.setProperty('--drawer-bg', 'rgba(255,255,255,0.75)')
      root.style.setProperty('--drawer-border', 'rgba(0,0,0,0.1)')
    }
  }, [])

  // Watch for theme changes and update glass variables
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const isDark = document.documentElement.classList.contains('dark')
      const root = document.documentElement
      if (isDark) {
        root.style.setProperty('--guide-bg', 'rgba(10,10,10,0.5)')
        root.style.setProperty('--guide-border', 'rgba(255,255,255,0.1)')
        root.style.setProperty('--guide-shadow', '0 24px 80px rgba(0,0,0,0.3), 0 0 0 1px rgba(24,226,153,0.06), inset 0 1px 0 rgba(255,255,255,0.06)')
        root.style.setProperty('--drawer-bg', 'rgba(10,10,10,0.6)')
        root.style.setProperty('--drawer-border', 'rgba(255,255,255,0.08)')
      } else {
        root.style.setProperty('--guide-bg', 'rgba(255,255,255,0.7)')
        root.style.setProperty('--guide-border', 'rgba(0,0,0,0.15)')
        root.style.setProperty('--guide-shadow', '0 24px 80px rgba(0,0,0,0.12), 0 0 0 1px rgba(24,226,153,0.1), inset 0 1px 0 rgba(255,255,255,0.5)')
        root.style.setProperty('--drawer-bg', 'rgba(255,255,255,0.75)')
        root.style.setProperty('--drawer-border', 'rgba(0,0,0,0.1)')
      }
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  const nextStep = useCallback(() => {
    if (currentStep < totalSteps - 1) {
      setCurrentStep(currentStep + 1)
    } else {
      finish()
    }
  }, [currentStep, totalSteps])

  const prevStep = useCallback(() => {
    if (currentStep > 0) setCurrentStep(currentStep - 1)
  }, [currentStep])

  const finish = useCallback(() => {
    localStorage.setItem('wx-assist-guided', '1')
    // Clear highlights
    document.querySelectorAll('.guide-highlight').forEach(el => {
      el.classList.remove('guide-highlight', 'guide-highlight-active')
    })
    // Clean up CSS variables
    const root = document.documentElement
    root.style.removeProperty('--guide-bg')
    root.style.removeProperty('--guide-border')
    root.style.removeProperty('--guide-shadow')
    root.style.removeProperty('--drawer-bg')
    root.style.removeProperty('--drawer-border')
    // Switch to dashboard after guide
    onTabChange('dashboard')
    onComplete()
  }, [onComplete])

  // Render the mock page for current step
  const MockPage = getMockPage(step.tabId, restrictedEnabled)

  return (
    <>
      {/* Main content shrinks when drawer is open */}
      <div className={drawerType ? 'mr-[420px] transition-[margin] duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]' : 'transition-[margin] duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]'}>
        <div className="sticky top-0 z-30 px-8 py-4 flex items-center justify-between border-b border-border-main transition-colors duration-300" style={{ background: 'var(--bg-main)', backdropFilter: 'blur(12px)' }}>
          <h2 className="text-sm font-semibold tracking-tight text-text-main">{TAB_LABELS[step.tabId]}</h2>
        </div>
        <div className="p-8">
          <MockPage />
        </div>
      </div>

      {/* AI Chat Drawer */}
      <AnimatePresence>
        {drawerType && <AIDrawer type={drawerType} onClose={() => setDrawerType(null)} />}
      </AnimatePresence>

      {/* Guide overlay + card */}
      <div className="fixed inset-0 z-[90] pointer-events-none">
        <GuideCard
          step={step}
          stepIndex={currentStep}
          totalSteps={totalSteps}
          onPrev={prevStep}
          onNext={nextStep}
          onSkip={finish}
          onGoToStep={(i) => { if (i >= 0 && i < totalSteps) setCurrentStep(i) }}
        />
      </div>
    </>
  )
}
