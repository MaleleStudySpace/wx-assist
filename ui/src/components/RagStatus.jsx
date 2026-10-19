import { useEffect, useState } from 'react'
import { API_BASE } from './SharedComponents'

const phases = { idle: '等待启动', disabled: '未开启', loading: '正在加载模型与索引', ready: '已就绪', error: '需要处理' }

export default function RagStatus({ form, update }) {
  const [status, setStatus] = useState(null)
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    let cancelled = false
    let timer
    const controller = new AbortController()
    async function poll() {
      try {
        const response = await fetch(`${API_BASE}/api/rag/status`, { signal: controller.signal })
        if (!response.ok) throw new Error('status unavailable')
        const data = await response.json()
        if (!cancelled) { setStatus(data); setUnavailable(false) }
      } catch {
        if (!cancelled) setUnavailable(true)
      } finally {
        if (!cancelled) timer = setTimeout(poll, 2000)
      }
    }
    poll()
    return () => { cancelled = true; clearTimeout(timer); controller.abort() }
  }, [])

  const jobs = Object.entries(status?.jobs || {})
  const busy = jobs.some(([, job]) => ['preparing', 'running'].includes(job.state))
  const hardware = status?.hardware || {}
  const selectClass = 'rounded-lg border border-border-main bg-bg-card px-3 py-2 text-sm text-text-main'

  return (
    <section className="rounded-2xl border border-border-main bg-bg-card p-5 space-y-4" aria-label="语义索引">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text-main">语义索引</h3>
        <span className="text-xs text-text-muted" role="status">
          {unavailable ? '暂时无法获取进度' : busy ? '正在建立索引' : phases[status?.phase] || '读取状态中'}
        </span>
      </div>
      {form && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-4">
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              计算设备
              <select className={selectClass} value={form.rag_device || 'auto'} onChange={e => update('rag_device', e.target.value)}>
                <option value="auto">自动选择（推荐）</option>
                <option value="cpu">CPU</option>
                <option value="directml">Windows 显卡加速（DirectML）</option>
                <option value="cuda">NVIDIA CUDA</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              每批处理数量
              <select className={selectClass} value={form.rag_batch_size || 16} onChange={e => update('rag_batch_size', Number(e.target.value))}>
                <option value={8}>8（低内存 / 2 GB 显存）</option>
                <option value={16}>16（推荐）</option>
                <option value={32}>32</option>
                <option value={64}>64（需要更多内存）</option>
              </select>
            </label>
          </div>
          <p className="text-xs text-text-muted leading-relaxed">保存后重启生效。显卡加速需要应用包含对应支持；不可用或显存不足时自动使用 CPU。CPU 模式可降低显卡占用。</p>
        </div>
      )}
      {hardware.active && <p className="text-xs text-text-muted">当前设备：{hardware.label} · 每批 {hardware.batch_size} 项</p>}
      {hardware.fallback_reason && <p className="text-xs text-amber-600">{hardware.fallback_reason}</p>}
      {status?.error && <p role="alert" className="text-sm text-red-500">{status.error}</p>}
      {jobs.map(([name, job]) => {
        const percent = job.total ? Math.min(100, Math.floor(job.completed / job.total * 100)) : job.state === 'done' ? 100 : 0
        return (
          <div key={name} className="space-y-1.5">
            <div className="flex justify-between gap-3 text-xs text-text-muted">
              <span>{name}</span>
              <span>{job.state === 'preparing' ? '统计待处理内容' : `${job.completed.toLocaleString()} / ${job.total.toLocaleString()} ${job.unit} · ${percent}%`}{job.state === 'done' ? ' · 完成' : job.state === 'paused' ? ' · 已暂停' : ''}</span>
            </div>
            <progress className="w-full h-2 accent-emerald-500" aria-label={`${name}索引进度`} max={100} value={percent} />
            {job.state === 'running' && <p className="text-xs text-text-muted">已运行 {Math.floor(job.elapsed_seconds / 60)} 分 {job.elapsed_seconds % 60} 秒</p>}
            {job.error && <p className="text-xs text-red-500">{job.error}</p>}
          </div>
        )
      })}
      {busy && <p className="text-xs text-text-muted">正在本地处理历史内容，可继续使用其他功能。首次建立索引可能需要较长时间，重启后会从已保存的进度继续。</p>}
    </section>
  )
}
