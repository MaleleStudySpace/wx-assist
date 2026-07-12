import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle, ArrowRight, Spinner, XCircle, Warning, MagnifyingGlass, CircleNotch, Lightning, ChatCircle, CaretDown, CaretRight, Folder } from '@phosphor-icons/react'
import { Field, Select, Input, Toggle, spring, API_BASE } from './SharedComponents'

const API = API_BASE

// ── Step 1: Key Extraction & Diagnostics ──────────────────────────────

export function Step1Prepare({ data, updateData, onDone }) {
  const [phase, setPhase] = useState('idle') // idle | extracting | done | timeout | error
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [isManual, setIsManual] = useState(false)
  const [manualKey, setManualKey] = useState(data.key || '')
  const [manualWxid, setManualWxid] = useState(data.wxid || '')
  const [manualDbPath, setManualDbPath] = useState(data.db_path || '')

  // ── File picker for db_path ──────────────────────────────────
  const [showDbFilePicker, setShowDbFilePicker] = useState(false)
  const [dbPickerPath, setDbPickerPath] = useState('')
  const [dbPickerEntries, setDbPickerEntries] = useState([])
  const [dbPickerLoading, setDbPickerLoading] = useState(false)
  const [dbPickerError, setDbPickerError] = useState('')
  const [dbPickerInput, setDbPickerInput] = useState('')
  const [dbPickerDriveList, setDbPickerDriveList] = useState([])
  const [dbSelectedFile, setDbSelectedFile] = useState('')

  async function loadDbPickerDir(path) {
    setDbPickerLoading(true)
    setDbPickerError('')
    try {
      const params = path ? `?path=${encodeURIComponent(path)}` : ''
      const res = await fetch(`${API}/api/browse${params}`)
      const d = await res.json()
      if (d.ok) {
        setDbPickerPath(d.current_path || '')
        setDbPickerInput(d.current_path || '')
        setDbPickerEntries(d.entries || [])
        setDbSelectedFile('')
      } else {
        setDbPickerError(d.error || '无法读取目录')
      }
    } catch {
      setDbPickerError('无法连接到服务器')
    }
    setDbPickerLoading(false)
  }

  function openDbFilePicker() {
    const initialPath = manualDbPath ? manualDbPath.split('\\').slice(0, -1).join('\\') : ''
    setDbPickerInput(initialPath)
    setShowDbFilePicker(true)
    setDbSelectedFile('')
    if (!dbPickerDriveList.length) {
      fetch(`${API}/api/browse`).then(r => r.json()).then(d => {
        if (d.ok && d.entries?.length > 0) setDbPickerDriveList(d.entries)
        else setDbPickerDriveList(['C', 'D', 'E', 'F', 'G'].map(l => ({ name: `${l}:`, path: `${l}:\\`, is_dir: true })))
      }).catch(() => setDbPickerDriveList(['C', 'D', 'E', 'F', 'G'].map(l => ({ name: `${l}:`, path: `${l}:\\`, is_dir: true }))))
    }
    if (initialPath) {
      loadDbPickerDir(initialPath)
    } else {
      loadDbPickerDriveList()
    }
  }

  async function loadDbPickerDriveList() {
    setDbPickerLoading(true)
    setDbPickerError('')
    setDbPickerPath('')
    setDbPickerInput('')
    try {
      const res = await fetch(`${API}/api/browse`)
      const d = await res.json()
      if (d.ok && d.entries?.length > 0) {
        setDbPickerDriveList(d.entries)
        setDbPickerEntries(d.entries)
      } else {
        const drives = ['C', 'D', 'E', 'F', 'G'].map(l => ({ name: `${l}:`, path: `${l}:\\`, is_dir: true }))
        setDbPickerDriveList(drives)
        setDbPickerEntries(drives)
      }
    } catch {
      const drives = ['C', 'D', 'E', 'F', 'G'].map(l => ({ name: `${l}:`, path: `${l}:\\`, is_dir: true }))
      setDbPickerDriveList(drives)
      setDbPickerEntries(drives)
    }
    setDbPickerLoading(false)
  }

  function dbPickerNavigateUp() {
    if (/^[A-Z]:\\?$/.test(dbPickerPath.replace(/\\$/, ''))) {
      loadDbPickerDriveList()
      return
    }
    const parts = dbPickerPath.split('\\').filter(Boolean)
    if (parts.length > 1) {
      loadDbPickerDir(parts.slice(0, -1).join('\\') + '\\')
    }
  }

  function dbPickerSwitchDrive(drivePath) {
    loadDbPickerDir(drivePath)
  }

  function selectDbFile() {
    if (dbSelectedFile) {
      setManualDbPath(dbSelectedFile)
    }
    setShowDbFilePicker(false)
  }

  // Open file picker when state changes
  useEffect(() => {
    if (showDbFilePicker) openDbFilePicker()
  }, [showDbFilePicker])

  // Save wechat config (formerly Step 2) before advancing to AI config
  async function saveWechatConfig(wxid, dbPath, key, wechatDataDir) {
    try {
      await fetch(`${API}/api/onboarding/step2`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wechat_backend: 'wcdb',
          wxid: wxid || '',
          db_path: dbPath || '',
          key: key || '',
          wechat_data_dir: wechatDataDir || '',
        }),
      })
    } catch {}
  }

  // Pre-flight diagnostics state
  const [diagnostics, setDiagnostics] = useState(null)
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(true)
  const [diagnosticsError, setDiagnosticsError] = useState('')

  async function fetchDiagnostics() {
    setDiagnosticsLoading(true)
    setDiagnosticsError('')
    try {
      const res = await fetch(`${API}/api/onboarding/diagnose`)
      const d = await res.json()
      if (d.ok) {
        setDiagnostics(d.diagnostics)
      } else {
        setDiagnosticsError(d.error || '获取诊断信息失败')
      }
    } catch {
      setDiagnosticsError('无法连接服务器，请确保机器人后端已启动')
    }
    setDiagnosticsLoading(false)
  }

  useEffect(() => {
    fetchDiagnostics()
  }, [])

  const isManualValid =
    manualKey.trim().length === 64 &&
    /^[0-9a-fA-F]+$/.test(manualKey.trim()) &&
    manualWxid.trim().length > 0 &&
    manualDbPath.trim().length > 0;

  async function handleExtract() {
    setBusy(true)
    setPhase('extracting')
    setMsg('')
    try {
      const startRes = await fetch(`${API}/api/onboarding/step1`, { method: 'POST' })
      const start = await startRes.json()
      if (!start.ok) {
        setPhase('error')
        setMsg(start.message || '启动失败')
        setBusy(false)
        return
      }

      const poll = setInterval(async () => {
        try {
          const res = await fetch(`${API}/api/onboarding/step1-status`)
          const s = await res.json()

          if (s.phase === 'waiting_exit' || s.phase === 'waiting_login'
              || s.phase === 'hooking' || s.phase === 'hooking_restart') {
            setMsg(s.message || '')
          } else if ((s.phase === 'done' || s.phase === 'done_need_step2') && s.result) {
            clearInterval(poll)
            updateData({
              key: s.result.key,
              wxid: s.result.wxid || '',
              db_path: s.result.db_path || '',
              wechat_data_dir: s.result.wechat_data_dir || '',
            })
            if (s.result.skip_step2) {
              // wxid/db_path auto-detected — skip Step 2
              setPhase('done')
              setBusy(false)
              saveWechatConfig(s.result.wxid, s.result.db_path, s.result.key, s.result.wechat_data_dir || '').then(() => onDone(true))
            } else {
              // wxid/db_path not detected — show message, proceed to Step 2
              setPhase('done_need_step2')
              setBusy(false)
              saveWechatConfig('', '', s.result.key, s.result.wechat_data_dir || '').then(() => onDone(false))
            }
          } else if (s.phase === 'timeout' || s.phase === 'error') {
            clearInterval(poll)
            setPhase(s.phase === 'timeout' ? 'timeout' : 'error')
            setMsg(s.message || '提取失败')
            setBusy(false)
          }
        } catch {}
      }, 1000)
    } catch {
      setPhase('error')
      setMsg('无法连接服务器')
      setBusy(false)
    }
  }

  function handleManualSubmit() {
    const wxid = manualWxid.trim()
    const dbPath = manualDbPath.trim()
    updateData({
      key: manualKey.trim(),
      wxid: wxid,
      db_path: dbPath,
    })
    // If wxid/db_path provided, skip Step 2; otherwise go to Step 2
    const skipStep2 = !!(wxid && dbPath)
    saveWechatConfig(wxid, dbPath, manualKey.trim()).then(() => onDone(skipStep2))
  }

  function renderChecklist() {
    if (diagnosticsLoading) {
      return (
        <div className="flex flex-col items-center justify-center py-12 space-y-3 bg-bg-raised border border-border-main rounded-2xl">
          <Spinner size={28} weight="bold" className="animate-spin text-brand-green" />
          <p className="text-sm text-text-muted font-mono">正在分析本地系统就绪状态...</p>
        </div>
      )
    }

    if (diagnosticsError) {
      return (
        <div className="p-6 bg-status-error-soft border border-status-error/20 rounded-2xl text-sm text-status-error flex items-center justify-between">
          <div className="flex items-center gap-2">
            <XCircle size={20} weight="fill" />
            <span>{diagnosticsError}</span>
          </div>
          <button
            onClick={fetchDiagnostics}
            className="px-4 py-2 bg-status-error/20 hover:bg-status-error/30 rounded-full text-xs font-semibold cursor-pointer transition-colors text-status-error"
          >
            重新连接
          </button>
        </div>
      )
    }

    if (!diagnostics) return null

    const items = [
      {
        key: 'python',
        title: 'Python 运行环境',
        desc: diagnostics.python.value,
        ok: diagnostics.python.ok,
        critical: true,
      },
      {
        key: 'requirements',
        title: 'Python 依赖库 (requirements.txt)',
        desc: diagnostics.requirements.ok
          ? diagnostics.requirements.value
          : `缺少依赖: ${diagnostics.requirements.missing.join(', ')}`,
        ok: diagnostics.requirements.ok,
        critical: true,
        help: !diagnostics.requirements.ok ? '请打开终端运行: pip install -r requirements.txt' : null,
      },
      {
        key: 'wechat',
        title: '微信电脑端状态',
        desc: diagnostics.wechat.value,
        ok: diagnostics.wechat.ok,
        critical: false,
        help: !diagnostics.wechat.ok ? '自动连接需要微信处于登录状态。若微信已登录但检测为未运行，请检查微信版本。' : null,
      },
      {
        key: 'env',
        title: '本地环境配置文件 (.env)',
        desc: diagnostics.env.value,
        ok: diagnostics.env.ok,
        critical: false,
      },
      {
        key: 'db',
        title: '本地数据库读写权限',
        desc: diagnostics.db.value,
        ok: diagnostics.db.ok,
        critical: true,
      }
    ]

    return (
      <div className="space-y-4">
        <div className="bg-bg-raised border border-border-main rounded-2xl divide-y divide-border-main/40">
          {items.map(item => (
            <div key={item.key} className="p-4 flex items-start justify-between gap-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-text-main">{item.title}</span>
                  {!item.ok && item.critical && (
                    <span className="text-xs bg-status-error-soft text-status-error px-2.5 py-0.5 rounded-full border border-status-error/30 font-bold">
                      阻塞项
                    </span>
                  )}
                </div>
                <p className={`text-xs ${item.ok ? 'text-text-muted' : 'text-status-warn'} font-mono`}>
                  {item.desc}
                </p>
                {item.help && (
                  <p className="text-[11px] text-text-muted bg-bg-main/45 p-2 rounded-2xl border border-border-main/30 font-mono mt-2 select-all leading-normal">
                    {item.help}
                  </p>
                )}
              </div>
              <div className="shrink-0 flex items-center h-5">
                {item.ok ? (
                  <CheckCircle size={20} weight="fill" className="text-brand-green" />
                ) : item.critical ? (
                  <XCircle size={20} weight="fill" className="text-status-error" />
                ) : (
                  <div className="w-5 h-5 rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center">
                    <span className="text-status-warn text-xs font-mono font-bold">!</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="flex justify-between items-center bg-bg-raised/30 px-4 py-3 rounded-2xl border border-border-main/40">
          <span className="text-xs text-text-muted">环境诊断能保障自动提取及数据库访问正常。</span>
          <button
            onClick={fetchDiagnostics}
            className="text-xs text-brand-green-hover dark:text-brand-green hover:underline transition-colors font-medium flex items-center gap-1 cursor-pointer font-semibold"
          >
            重新检测
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-4.5 rounded-full bg-brand-green" />
          <h3 className="text-base font-semibold tracking-tight text-text-main">环境准备与连接</h3>
        </div>
        <button
          onClick={() => setIsManual(!isManual)}
          className="text-xs text-brand-green-hover dark:text-brand-green hover:underline cursor-pointer font-medium"
        >
          {isManual ? '返回环境诊断' : '无法获取？手动配置'}
        </button>
      </div>

      <div className="bg-bg-card rounded-2xl p-1 space-y-6">
        {isManual ? (
          <div className="space-y-5">
            <p className="text-[14px] text-text-muted leading-relaxed">
              您在此可手动填写连接凭证和相关环境信息。
            </p>
            <Field label="连接凭证 (64位十六进制)" hint="获取到的64位 hex 凭证" error={manualKey && (manualKey.trim().length !== 64 || !/^[0-9a-fA-F]+$/.test(manualKey.trim())) ? '连接凭证格式不正确，必须为64位16进制字符' : null}>
              <Input
                type="password"
                value={manualKey}
                onChange={setManualKey}
                placeholder="例如：68a1f28b4c2..."
              />
            </Field>
            <Field label="微信账号 ID (wxid)" hint="当前微信账号的内部 ID (以 wxid_ 开头，或自定义微信号)">
              <Input
                value={manualWxid}
                onChange={setManualWxid}
                placeholder="例如：wxid_xxxxxxxxxxxxxx"
              />
            </Field>
            <Field label="聊天数据库路径 (db_path)" hint="微信设置 → 账号与存储 → 存储位置 → 数据目录下的 db_storage/session/">
              <div className="flex items-center gap-2">
                <Input
                  value={manualDbPath}
                  onChange={setManualDbPath}
                  placeholder="例如：C:\Users\...\db_storage\session\session.db"
                />
                <button
                  type="button"
                  onClick={() => setShowDbFilePicker(true)}
                  className="shrink-0 p-2.5 rounded-full bg-bg-raised border border-border-main hover:border-brand-green hover:bg-brand-green-light text-text-muted hover:text-brand-green transition-all cursor-pointer"
                  title="浏览"
                >
                  <Folder size={18} />
                </button>
              </div>
            </Field>
            <motion.button
              whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
              onClick={handleManualSubmit}
              disabled={!isManualValid}
              className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer ${
                isManualValid
                  ? 'bg-brand-green-hover text-white hover:opacity-90'
                  : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
              }`}
            >
              <ArrowRight size={18} /> 保存并下一步
            </motion.button>

            {/* ── File Picker Modal for db_path ────────────────────────── */}
            {showDbFilePicker && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowDbFilePicker(false)}>
                <div className="bg-bg-card border border-border-main rounded-2xl w-[520px] max-h-[70vh] flex flex-col shadow-xl" onClick={e => e.stopPropagation()}>
                  <div className="flex items-center justify-between px-5 py-3 border-b border-border-main">
                    <h4 className="text-sm font-semibold text-text-main">选择数据库文件</h4>
                    <button onClick={() => setShowDbFilePicker(false)} className="text-text-muted hover:text-text-main text-lg leading-none cursor-pointer">&times;</button>
                  </div>
                  {/* Path bar */}
                  <div className="px-4 py-2 border-b border-border-main/50 flex items-center gap-2">
                    <button onClick={dbPickerNavigateUp} className="text-xs text-brand-green hover:underline cursor-pointer shrink-0">↑ 上级</button>
                    <input
                      type="text" value={dbPickerInput}
                      onChange={e => setDbPickerInput(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') loadDbPickerDir(dbPickerInput) }}
                      className="flex-1 bg-bg-raised border border-border-main rounded-lg px-3 py-1.5 text-xs font-mono text-text-main focus:outline-none focus:border-brand-green"
                    />
                    <button onClick={() => loadDbPickerDir(dbPickerInput)} className="text-xs text-brand-green hover:underline cursor-pointer shrink-0">前往</button>
                  </div>
                  {/* Entries */}
                  <div className="flex-1 overflow-y-auto px-3 py-2 min-h-[200px]">
                    {dbPickerLoading && <p className="text-xs text-text-muted text-center py-8">加载中...</p>}
                    {dbPickerError && <p className="text-xs text-status-error text-center py-4">{dbPickerError}</p>}
                    {!dbPickerLoading && !dbPickerError && dbPickerEntries.map((entry, i) => (
                      <button
                        key={i}
                        onClick={() => {
                          if (entry.is_dir) {
                            loadDbPickerDir(entry.path)
                          } else {
                            setDbSelectedFile(entry.path)
                          }
                        }}
                        className={`w-full text-left px-3 py-2 text-sm rounded-lg flex items-center gap-2 transition-colors cursor-pointer ${
                          dbSelectedFile === entry.path
                            ? 'bg-brand-green/10 text-brand-green-hover dark:text-brand-green'
                            : 'text-text-main hover:bg-bg-raised'
                        }`}
                      >
                        <span>{entry.is_dir ? '📁' : '📄'}</span>
                        <span className="truncate">{entry.name}</span>
                        {!entry.is_dir && <span className="ml-auto text-[10px] text-text-muted font-mono">{entry.size || ''}</span>}
                      </button>
                    ))}
                    {!dbPickerLoading && !dbPickerError && dbPickerEntries.length === 0 && (
                      <p className="text-xs text-text-muted text-center py-8">空目录</p>
                    )}
                  </div>
                  {/* Footer: drive list + select */}
                  <div className="border-t border-border-main px-4 py-3 space-y-2">
                    {dbPickerDriveList.length > 0 && (
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {dbPickerDriveList.map((d, i) => (
                          <button
                            key={i}
                            onClick={() => dbPickerSwitchDrive(d.path)}
                            className={`px-2.5 py-1 text-xs font-mono rounded-full border cursor-pointer transition-colors ${
                              dbPickerPath?.startsWith(d.path)
                                ? 'bg-brand-green/10 border-brand-green/30 text-brand-green-hover dark:text-brand-green'
                                : 'bg-bg-raised border-border-main text-text-muted hover:text-text-main hover:border-text-muted/30'
                            }`}
                          >{d.name}</button>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center justify-between">
                      {dbSelectedFile && <p className="text-xs text-text-muted font-mono truncate max-w-[300px]" title={dbSelectedFile}>已选: {dbSelectedFile.split('\\').pop()}</p>}
                      {!dbSelectedFile && <p className="text-xs text-text-muted">点击文件选择，点击文件夹进入</p>}
                      <button
                        onClick={selectDbFile}
                        disabled={!dbSelectedFile}
                        className={`px-4 py-1.5 text-xs font-semibold rounded-full transition-all cursor-pointer ${
                          dbSelectedFile
                            ? 'bg-brand-green-hover text-white hover:opacity-90'
                            : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
                        }`}
                      >选择此文件</button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <>
            {phase === 'idle' && (
              <div className="space-y-6">
                <p className="text-[14px] text-text-muted leading-relaxed">
                  摘星需要与微信建立连接以读取聊天记录。连接过程无侵入，不会影响微信正常使用。
                </p>

                {renderChecklist()}

                <div className="pt-4 border-t border-border-main/40 flex items-center gap-4">
                  <motion.button
                    whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
                    onClick={handleExtract}
                    disabled={diagnosticsLoading || (diagnostics && !diagnostics.wechat.ok)}
                    className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer ${
                      diagnostics && diagnostics.wechat.ok
                        ? 'bg-brand-green-hover text-white hover:opacity-90 animate-pulse'
                        : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
                    }`}
                  >
                    开始自动获取
                  </motion.button>
                  {diagnostics && !diagnostics.wechat.ok && (
                    <span className="text-xs text-status-warn bg-status-warn-soft border border-status-warn/20 px-4 py-2 rounded-full font-medium">
                      请登录微信电脑端，否则无法自动获取
                    </span>
                  )}
                </div>
              </div>
            )}

            {phase === 'extracting' && (
              <div className="space-y-6">
                <p className="text-[14px] text-text-muted leading-relaxed">
                  正在协同微信进程，获取本地数据连接凭证，请勿关闭微信。
                </p>
                <div className="bg-bg-raised border border-border-main rounded-2xl p-6 flex items-start gap-4">
                  <Spinner size={24} weight="bold" className="animate-spin text-brand-green shrink-0 mt-0.5" />
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-text-main">自动连接中...</p>
                    <p className="text-xs text-text-muted font-mono">{msg || '等待微信窗口激活...'}</p>
                  </div>
                </div>
                {/* Mini-terminal keeps dark layout for developer style */}
                <div className="bg-bg-card border border-border-main rounded-2xl p-4 font-mono text-xs text-text-muted space-y-1">
                  <div className="flex justify-between border-b border-border-main/30 pb-1 mb-2 text-text-muted font-semibold">
                    <span>连接状态</span>
                    <span>ACTIVE</span>
                  </div>
                  <div>[1] 正在建立连接...</div>
                  {msg.includes('waiting_exit') && <div className="text-status-warn">[!] 检测到微信在运行，请先退出微信以便重新挂钩...</div>}
                  {msg.includes('waiting_login') && <div className="text-brand-green">[+] 微信已重新挂钩，请在弹出的微信界面进行登录...</div>}
                  {msg && <div className="text-text-muted">&gt; {msg}</div>}
                </div>
                <button
                  onClick={() => setPhase('idle')}
                  className="px-4 py-2 bg-bg-raised hover:bg-bg-card text-xs text-text-muted hover:text-text-main rounded-full border border-border-main cursor-pointer transition-colors"
                >
                  取消并返回
                </button>
              </div>
            )}

            {phase === 'timeout' && (
              <div className="space-y-6">
                <div className="bg-status-error-soft border border-status-error/20 rounded-2xl p-6 flex items-start gap-4 text-status-error">
                  <XCircle size={24} weight="fill" className="text-status-error shrink-0" />
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-status-error">连接超时</p>
                    <p className="text-xs text-text-muted">{msg || '连接超时，请确保您成功登录了微信。'}</p>
                  </div>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleExtract}
                    className="px-4 py-2 bg-brand-green-hover text-white hover:opacity-90 text-sm font-semibold rounded-full cursor-pointer transition-colors"
                  >
                    重试自动获取
                  </button>
                  <button
                    onClick={() => setPhase('idle')}
                    className="px-4 py-2 bg-bg-raised hover:bg-bg-card text-sm text-text-muted rounded-full border border-border-main cursor-pointer transition-colors"
                  >
                    返回诊断
                  </button>
                </div>
              </div>
            )}

            {phase === 'error' && (
              <div className="space-y-6">
                <div className="bg-status-error-soft border border-status-error/20 rounded-2xl p-6 flex items-start gap-4 text-status-error">
                  <XCircle size={24} weight="fill" className="text-status-error shrink-0" />
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-status-error">自动提取失败</p>
                    <p className="text-xs text-text-muted">{msg}</p>
                  </div>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleExtract}
                    className="px-4 py-2 bg-brand-green-hover text-white hover:opacity-90 text-sm font-semibold rounded-full cursor-pointer transition-colors"
                  >
                    重新获取
                  </button>
                  <button
                    onClick={() => setPhase('idle')}
                    className="px-4 py-2 bg-bg-raised hover:bg-bg-card text-sm text-text-muted rounded-full border border-border-main cursor-pointer transition-colors"
                  >
                    返回诊断
                  </button>
                </div>
              </div>
            )}

            {phase === 'done' && (
              <div className="space-y-5">
                <div className="bg-brand-green-light border border-brand-green/20 rounded-2xl p-5 flex items-center gap-3">
                  <CheckCircle size={24} weight="fill" className="text-brand-green-hover dark:text-brand-green" />
                  <div>
                    <p className="text-sm font-semibold text-brand-green-hover dark:text-brand-green">连接成功</p>
                    <p className="text-xs text-text-muted">系统配置已就绪</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
                    <p className="text-xs text-text-muted mb-1 font-semibold">微信账号 wxid</p>
                    <p className="text-sm font-mono text-text-main truncate font-bold">{data.wxid || '—'}</p>
                  </div>
                  <div className="bg-bg-raised border border-border-main rounded-2xl p-4">
                    <p className="text-xs text-text-muted mb-1 font-semibold">数据库文件</p>
                    <p className="text-xs font-mono text-text-main truncate">{data.db_path ? data.db_path.split('\\').slice(-2).join('\\') : '—'}</p>
                  </div>
                </div>
              </div>
            )}

            {phase === 'done_need_step2' && (
              <div className="space-y-5">
                <div className="bg-brand-green-light border border-brand-green/20 rounded-2xl p-5 flex items-center gap-3">
                  <CheckCircle size={24} weight="fill" className="text-brand-green-hover dark:text-brand-green" />
                  <div>
                    <p className="text-sm font-semibold text-brand-green-hover dark:text-brand-green">凭证获取成功</p>
                    <p className="text-xs text-text-muted">请继续配置数据目录</p>
                  </div>
                </div>
                <div className="bg-status-warn-soft border border-status-warn/20 rounded-2xl p-4 flex items-start gap-3">
                  <Warning size={20} weight="fill" className="text-status-warn shrink-0 mt-0.5" />
                  <div className="text-sm text-status-warn">
                    <p className="font-semibold mb-1">未能自动检测到数据目录</p>
                    <p className="text-xs text-text-muted font-normal">请在下一步手动选择微信数据目录，系统将自动推导出账号信息。</p>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Step 2: Data Directory Config ─────────────────────────────────────

export function Step2DataDir({ data, updateData, onDone }) {
  const [busy, setBusy] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [detectResult, setDetectResult] = useState(null)
  const [detectError, setDetectError] = useState('')
  const [browseOpen, setBrowseOpen] = useState(false)
  const [browsePath, setBrowsePath] = useState('')
  const [browseEntries, setBrowseEntries] = useState([])
  const [browseLoading, setBrowseLoading] = useState(false)
  const [browseError, setBrowseError] = useState('')
  const [browseInput, setBrowseInput] = useState('')
  const [dataDir, setDataDir] = useState(data.wechat_data_dir || '')
  const [driveList, setDriveList] = useState([])

  // Auto-detect on mount
  useEffect(() => {
    handleAutoDetect()
  }, [])

  async function handleAutoDetect() {
    setDetecting(true)
    setDetectError('')
    setDetectResult(null)
    try {
      // Try default detection (no path = auto-detect from Documents)
      const res = await fetch(`${API}/api/wechat-data-dir/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: '' }),
      })
      const d = await res.json()
      if (d.ok && d.found) {
        setDetectResult(d)
        // Auto-fill the first account's data dir
        if (d.accounts?.length > 0) {
          // The detected dir is the parent containing wxid_* folders
          // We need to figure out the base dir from the API response
        }
      } else if (d.ok && !d.found) {
        setDetectResult(d)
      } else {
        setDetectError(d.error || '自动检测失败')
      }
    } catch {
      setDetectError('无法连接到服务器')
    }
    setDetecting(false)
  }

  async function handleDetectWithPath(path) {
    const trimmed = (path || '').trim()
    if (!trimmed) {
      setDetectError('请先输入或选择目录路径')
      setTimeout(() => setDetectError(''), 4000)
      return
    }
    setDetecting(true)
    setDetectError('')
    setDetectResult(null)
    try {
      const res = await fetch(`${API}/api/wechat-data-dir/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: trimmed }),
      })
      const d = await res.json()
      if (d.ok) {
        setDetectResult(d)
        if (d.found) {
          setDataDir(trimmed)
          updateData({ wechat_data_dir: trimmed })
        }
      } else {
        setDetectError(d.error || '检测失败')
        setTimeout(() => setDetectError(''), 5000)
      }
    } catch {
      setDetectError('无法连接到服务器')
      setTimeout(() => setDetectError(''), 5000)
    }
    setDetecting(false)
  }

  // ── Browse API ────────────────────────────────────────────────

  async function loadBrowseDir(path) {
    setBrowseLoading(true)
    setBrowseError('')
    try {
      const params = path ? `?path=${encodeURIComponent(path)}` : ''
      const res = await fetch(`${API}/api/browse${params}`)
      const d = await res.json()
      if (d.ok) {
        setBrowsePath(d.current_path || '')
        setBrowseInput(d.current_path || '')
        setBrowseEntries(d.entries || [])
      } else {
        setBrowseError(d.error || '无法读取目录')
      }
    } catch {
      setBrowseError('无法连接到服务器')
    }
    setBrowseLoading(false)
  }

  function openBrowse() {
    const initialPath = dataDir || ''
    setBrowseInput(initialPath)
    setBrowseOpen(true)
    // Load drive list for the footer
    if (!driveList.length) {
      fetch(`${API}/api/browse`).then(r => r.json()).then(d => {
        if (d.ok && d.entries?.length > 0) setDriveList(d.entries)
        else {
          const drives = ['C', 'D', 'E', 'F', 'G'].map(l => ({ name: `${l}:`, path: `${l}:\\`, is_dir: true }))
          setDriveList(drives)
        }
      }).catch(() => {
        const drives = ['C', 'D', 'E', 'F', 'G'].map(l => ({ name: `${l}:`, path: `${l}:\\`, is_dir: true }))
        setDriveList(drives)
      })
    }
    if (initialPath) {
      loadBrowseDir(initialPath)
    } else {
      loadDriveList()
    }
  }

  function handleBrowseGo() {
    const trimmed = browseInput.trim()
    if (trimmed) {
      setBrowseError('')
      loadBrowseDir(trimmed)
    }
  }

  function handleBrowseInputKeyDown(e) {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleBrowseGo()
    }
  }

  function navigateUp() {
    // If at drive root (e.g. "C:\" or "C:"), go back to drive list
    if (/^[A-Z]:\\?$/.test(browsePath.replace(/\\$/, ''))) {
      loadDriveList()
      return
    }
    const parts = browsePath.split('\\').filter(Boolean)
    if (parts.length > 1) {
      const parent = parts.slice(0, -1).join('\\') + '\\'
      loadBrowseDir(parent)
    }
  }

  async function loadDriveList() {
    setBrowseLoading(true)
    setBrowseError('')
    setBrowsePath('')
    setBrowseInput('')
    try {
      const res = await fetch(`${API}/api/browse`)
      const d = await res.json()
      if (d.ok && d.entries?.length > 0) {
        setDriveList(d.entries)
        setBrowseEntries(d.entries)
      } else {
        // Fallback: construct drive list
        const drives = []
        for (const letter of ['C', 'D', 'E', 'F', 'G']) {
          drives.push({ name: `${letter}:`, path: `${letter}:\\`, is_dir: true })
        }
        setDriveList(drives)
        setBrowseEntries(drives)
      }
    } catch {
      // Fallback drive list
      const drives = []
      for (const letter of ['C', 'D', 'E', 'F', 'G']) {
        drives.push({ name: `${letter}:`, path: `${letter}:\\`, is_dir: true })
      }
      setDriveList(drives)
      setBrowseEntries(drives)
    }
    setBrowseLoading(false)
  }

  function switchToDrive(drivePath) {
    loadBrowseDir(drivePath)
  }

  function navigateTo(entryPath) {
    loadBrowseDir(entryPath)
  }

  function selectCurrentPath() {
    setDataDir(browsePath)
    updateData({ wechat_data_dir: browsePath })
    setBrowseOpen(false)
    // Auto-detect after selecting
    handleDetectWithPath(browsePath)
  }

  // ── Save & Next ────────────────────────────────────────────────

  async function handleNext() {
    if (!dataDir.trim()) {
      setDetectError('请先选择数据目录')
      setTimeout(() => setDetectError(''), 4000)
      return
    }
    setBusy(true)
    try {
      const res = await fetch(`${API}/api/onboarding/step2`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          wechat_backend: 'wcdb',
          wechat_data_dir: dataDir.trim(),
        }),
      })
      const d = await res.json()
      if (d.ok) {
        // Update local data with derived wxid/db_path
        updateData({
          wechat_data_dir: dataDir.trim(),
          wxid: d.wxid || data.wxid || '',
          db_path: d.db_path || data.db_path || '',
        })
        onDone()
      } else {
        setDetectError(d.error || '保存失败')
        setTimeout(() => setDetectError(''), 5000)
      }
    } catch {
      setDetectError('无法连接到服务器')
      setTimeout(() => setDetectError(''), 5000)
    }
    setBusy(false)
  }

  const canProceed = detectResult?.found && dataDir.trim()

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <div className="w-1.5 h-4.5 rounded-full bg-brand-green" />
        <h3 className="text-base font-semibold tracking-tight text-text-main">数据目录配置</h3>
      </div>

      <div className="space-y-6 mt-4">
        <p className="text-[14px] text-text-muted leading-relaxed">
          摘星需要定位微信数据目录以读取聊天记录。该目录包含以 <code className="bg-bg-raised px-1.5 py-0.5 rounded font-mono text-xs">wxid_</code> 开头的账号文件夹。
        </p>

        {/* Auto-detect result */}
        {detecting && (
          <div className="bg-bg-raised border border-border-main rounded-2xl p-5 flex items-center gap-3">
            <Spinner size={20} weight="bold" className="animate-spin text-brand-green" />
            <p className="text-sm text-text-muted">正在自动检测数据目录...</p>
          </div>
        )}

        {detectResult && !detecting && (
          <div className={`p-4 rounded-2xl border ${
            detectResult.found
              ? 'bg-brand-green-light border-brand-green/20'
              : 'bg-status-warn-soft border-status-warn/20'
          }`}>
            {detectResult.found ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <CheckCircle size={18} weight="fill" className="text-brand-green-hover dark:text-brand-green" />
                  <span className="text-sm font-semibold text-brand-green-hover dark:text-brand-green">{detectResult.message}</span>
                </div>
                {detectResult.accounts?.map((acct, i) => (
                  <div key={i} className="flex items-center gap-3 text-xs font-mono bg-bg-main/60 border border-border-main rounded-xl px-3 py-2">
                    <span className="text-text-main font-semibold">{acct.wxid}</span>
                    <span className="text-text-muted">·</span>
                    <span className={acct.has_session_db ? 'text-brand-green-hover dark:text-brand-green' : 'text-status-error'}>
                      {acct.has_session_db ? '✓ 数据库已就绪' : '✗ 未找到数据库'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex items-start gap-2">
                <Warning size={18} weight="fill" className="text-status-warn shrink-0 mt-0.5" />
                <div>
                  <span className="text-sm text-status-warn font-semibold">未检测到数据目录</span>
                  <p className="text-xs text-text-muted mt-1">请点击「浏览」手动选择微信数据目录（包含 wxid_* 文件夹的父目录）</p>
                </div>
              </div>
            )}
          </div>
        )}

        {detectError && (
          <div className="flex items-center gap-2 px-4 py-2.5 bg-status-error-soft border border-status-error/20 rounded-full text-sm text-status-error">
            <Warning size={16} weight="fill" className="text-status-error" />
            <span>{detectError}</span>
          </div>
        )}

        {/* Data dir input + browse + detect */}
        <Field label="微信数据目录" hint="微信设置 → 账号与存储 → 存储位置">
          <div className="flex items-start gap-2">
            <div className="flex-1 relative">
              <input
                type="text"
                value={dataDir}
                onChange={e => { setDataDir(e.target.value); updateData({ wechat_data_dir: e.target.value }); setDetectResult(null) }}
                placeholder="例如：D:\vxchat\xwechat_files"
                className="w-full bg-bg-raised border border-border-main rounded-full pl-5 pr-5 py-2.5 text-[14px] text-text-main
                           placeholder:text-text-muted font-mono tabular-nums
                           focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
                           transition-all duration-200
                           hover:border-text-muted/30 dark:hover:border-text-muted/40"
              />
              {dataDir && (
                <button
                  type="button"
                  onClick={() => { setDataDir(''); updateData({ wechat_data_dir: '' }); setDetectResult(null); setDetectError('') }}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-status-error text-lg leading-none transition-colors cursor-pointer"
                  title="清除"
                >&times;</button>
              )}
            </div>
            <button
              type="button"
              onClick={openBrowse}
              className="shrink-0 px-4 py-2.5 bg-bg-main border border-border-main rounded-full text-[13px] text-text-main font-medium hover:border-brand-green hover:text-brand-green-hover transition-colors cursor-pointer"
            >
              浏览...
            </button>
            {dataDir.trim() && (
              <button
                type="button"
                onClick={() => handleDetectWithPath(dataDir)}
                disabled={detecting}
                className="shrink-0 px-4 py-2.5 bg-brand-green-light border border-brand-green/20 rounded-full text-[13px] text-brand-green-hover dark:text-brand-green font-semibold hover:bg-brand-green/10 transition-colors cursor-pointer disabled:opacity-50"
              >
                {detecting ? (
                  <span className="flex items-center gap-1.5">
                    <svg className="animate-spin h-3.5 w-3.5" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    检测中
                  </span>
                ) : '检测'}
              </button>
            )}
          </div>
        </Field>

        {/* Next button */}
        <div className="pt-2">
          <motion.button
            whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
            onClick={handleNext}
            disabled={!canProceed || busy}
            className={`w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer disabled:opacity-50 ${
              canProceed
                ? 'bg-brand-green-hover text-white hover:opacity-90'
                : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
            }`}
          >
            {busy ? <Spinner size={18} weight="bold" className="animate-spin" /> : <><ArrowRight size={18} /> 下一步</>}
          </motion.button>
        </div>
      </div>

      {/* ── Directory Browser Modal ────────────────────────────────── */}
      {browseOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-main/60 backdrop-blur-sm" onClick={() => setBrowseOpen(false)}>
          <div
            className="bg-bg-card border border-border-main rounded-2xl shadow-2xl w-[520px] max-h-[520px] flex flex-col overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-main/60">
              <h4 className="text-sm font-semibold text-text-main">选择微信数据目录</h4>
              <button
                type="button"
                onClick={() => setBrowseOpen(false)}
                className="text-text-muted hover:text-text-main transition-colors cursor-pointer leading-none text-lg"
              >&times;</button>
            </div>

            {/* Path input */}
            <div className="px-5 py-3 border-b border-border-main/40">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={browseInput}
                  onChange={e => setBrowseInput(e.target.value)}
                  onKeyDown={handleBrowseInputKeyDown}
                  placeholder="粘贴或输入路径，回车跳转..."
                  className="flex-1 bg-bg-raised border border-border-main rounded-full px-4 py-2 text-[13px] text-text-main placeholder:text-text-muted font-mono
                             focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15
                             transition-all duration-200 hover:border-text-muted/30"
                />
                <button
                  type="button"
                  onClick={handleBrowseGo}
                  disabled={!browseInput.trim()}
                  className="shrink-0 px-4 py-2 bg-brand-green-light border border-brand-green/20 rounded-full text-[13px] text-brand-green-hover dark:text-brand-green font-semibold hover:bg-brand-green/10 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default"
                >
                  跳转
                </button>
              </div>
            </div>

            {/* Path breadcrumb */}
            <div className="px-5 py-2.5 bg-bg-raised/50 border-b border-border-main/40">
              <div className="flex items-center gap-1.5 text-xs font-mono text-text-muted">
                <button
                  type="button"
                  onClick={navigateUp}
                  disabled={!browsePath || browsePath.length <= 3}
                  className="text-text-muted hover:text-text-main disabled:opacity-30 disabled:cursor-default cursor-pointer transition-colors"
                  title="上级目录"
                >↑</button>
                <span className="truncate">{browsePath || '此电脑'}</span>
              </div>
            </div>

            {/* Entry list */}
            <div className="flex-1 overflow-y-auto px-2 py-1.5">
              {browseLoading ? (
                <div className="flex items-center justify-center py-12">
                  <svg className="animate-spin h-5 w-5 text-text-muted" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                </div>
              ) : browseError ? (
                <div className="p-4 text-xs text-status-error text-center">{browseError}</div>
              ) : browseEntries.length === 0 ? (
                <div className="p-4 text-xs text-text-muted text-center">此目录为空</div>
              ) : (
                browseEntries.filter(e => e.is_dir).map((entry, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => navigateTo(entry.path)}
                    className="w-full text-left px-3 py-2 rounded-xl text-[13px] text-text-main hover:bg-bg-raised transition-colors cursor-pointer flex items-center gap-2.5 font-mono"
                  >
                    <span className="text-base shrink-0">📁</span>
                    <span className="truncate">{entry.name}</span>
                  </button>
                ))
              )}
            </div>

            {/* Footer with drive list */}
            <div className="border-t border-border-main/60">
              {/* Drive list */}
              {driveList.length > 0 && (
                <div className="px-5 py-2 border-b border-border-main/30 flex items-center gap-1.5">
                  <span className="text-[11px] text-text-muted mr-1">盘符:</span>
                  {driveList.map((drive, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => switchToDrive(drive.path)}
                      className={`px-2.5 py-1 rounded-md text-xs font-mono font-semibold transition-colors cursor-pointer ${
                        browsePath && browsePath.startsWith(drive.path)
                          ? 'bg-brand-green-light text-brand-green-hover dark:text-brand-green border border-brand-green/20'
                          : 'bg-bg-raised text-text-muted hover:text-text-main border border-border-main/40'
                      }`}
                    >{drive.name}</button>
                  ))}
                </div>
              )}
              <div className="px-5 py-3.5 flex items-center justify-between">
                <p className="text-xs text-text-muted truncate max-w-[340px] font-mono">
                  当前: {browsePath || '此电脑'}
                </p>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setBrowseOpen(false)}
                    className="px-4 py-2 rounded-full border border-border-main bg-bg-main text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer font-medium"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    onClick={selectCurrentPath}
                    disabled={!browsePath}
                    className="px-4 py-2 rounded-full bg-brand-green-hover text-white text-xs font-semibold hover:bg-[#0d8c5c] transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default"
                  >
                    选择此目录
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Step 3: AI Backend (3-step wizard for beginners) ───────────────────

export function Step3AIConfig({ data, updateData, onDone }) {
  const [substep, setSubstep] = useState(0) // 0=choose-provider, 1=guide, 2=fill-detect
  const [selectedProvider, setSelectedProvider] = useState(null) // 'deepseek' | 'custom' | null
  const [detecting, setDetecting] = useState(false)
  const [detectResult, setDetectResult] = useState(null)
  const [apiKey, setApiKey] = useState(data.ai_provider_api_key || '')
  const [customBaseUrl, setCustomBaseUrl] = useState(data.ai_provider_base_url || '')
  const [selectedModel, setSelectedModel] = useState(data.ai_provider_model || '')

  const isCustom = selectedProvider === 'custom'
  const DEEPSEEK_URL = 'https://api.deepseek.com'
  const DEEPSEEK_MODEL = 'deepseek-chat'

  const STEP_LABELS = ['选择平台', '获取 Key', '填写并检测']

  async function handleDetect() {
    const url = isCustom ? customBaseUrl.trim() : DEEPSEEK_URL
    const key = apiKey.trim()
    if (!key || (isCustom && !url)) return

    setDetecting(true)
    setDetectResult(null)
    try {
      const res = await fetch(`${API}/api/assistant/ai/detect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_url: url, api_key: key, provider_type: 'openai' }),
      })
      const result = await res.json()
      setDetectResult(result)
      if (result.provider_type) {
        updateData({
          ai_provider_base_url: url,
          ai_provider_api_key: key,
          ai_provider_type: result.provider_type,
        })
      }
    } catch {
      setDetectResult({ error: '网络请求失败，请检查站点 URL 和 Key' })
    }
    setDetecting(false)
  }

  async function handleFinish() {
    setDetecting(true)
    try {
      const url = isCustom ? customBaseUrl.trim() : DEEPSEEK_URL
      await fetch(`${API}/api/onboarding/step3`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ai_provider_base_url: url || '',
          ai_provider_api_key: apiKey.trim() || '',
          ai_provider_type: data.ai_provider_type || 'openai',
          ai_provider_model: data.ai_provider_model || '',
          ai_provider_extra_body: data.ai_provider_extra_body || '',
        }),
      })
      onDone()
    } catch {}
    setDetecting(false)
  }

  function handleSkip() {
    onDone()
  }

  const detectSuccess = detectResult?.provider_type && !detectResult?.error
  const hasModels = detectResult?.available_models?.length > 0
  const canFinish = detectSuccess && !detecting && (!hasModels || selectedModel)

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <div className="w-1.5 h-4.5 rounded-full bg-status-info" />
        <h3 className="text-base font-semibold tracking-tight text-text-main">AI 后端配置</h3>
        <span className="text-xs text-text-muted ml-1">可跳过，稍后在系统配置中设置</span>
      </div>

      {/* ── Substeps progress bar ── */}
      <div className="flex items-center gap-2 mb-6 px-1">
        {[0, 1, 2].map(i => (
          <div
            key={i}
            className={`h-1 rounded-full transition-all duration-400 ${
              i === substep ? 'w-10 bg-brand-green' : i < substep ? 'w-6 bg-brand-green/40' : 'w-6 bg-border-strong'
            }`}
          />
        ))}
        <span className="text-[11px] text-text-muted font-medium ml-auto">
          步骤 {substep + 1}/3 · {STEP_LABELS[substep]}
        </span>
      </div>

      <motion.div
        key={`substep-${substep}`}
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      >
        {/* ═══ Step 0: Choose Provider ═══ */}
        {substep === 0 && (
          <div className="space-y-5">
            <p className="text-[14px] text-text-muted leading-relaxed">
              选一个 AI 平台，摘星就能调用 AI 能力（群摘要、AI 对话、朋友圈分析等）。
            </p>

            <div className="flex flex-col gap-3">
              {/* ── DeepSeek ── */}
              <button
                onClick={() => setSelectedProvider('deepseek')}
                className={`flex items-center gap-4 p-4 rounded-2xl border-2 transition-all cursor-pointer text-left ${
                  selectedProvider === 'deepseek'
                    ? 'border-brand-green bg-bg-raised'
                    : 'border-border-main bg-bg-card hover:border-border-strong hover:bg-bg-raised'
                }`}
              >
                <div className="w-11 h-11 rounded-xl bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-lg font-bold shrink-0 text-brand-green">
                  D
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-text-main flex items-center gap-2">
                    DeepSeek
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-brand-green-light border border-brand-green/20 text-brand-green">推荐</span>
                  </div>
                  <div className="text-[12px] text-text-muted mt-0.5 leading-relaxed">
                    中文最强 · 价格最低 · 注册简单<br />
                    新用户送 500 万 tokens
                  </div>
                </div>
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                  selectedProvider === 'deepseek' ? 'bg-brand-green border-brand-green' : 'border-border-strong'
                }`}>
                  {selectedProvider === 'deepseek' && <span className="text-[10px] text-white font-bold">✓</span>}
                </div>
              </button>

              {/* ── Other compatible ── */}
              <button
                onClick={() => setSelectedProvider('custom')}
                className={`flex items-center gap-4 p-4 rounded-2xl border-2 transition-all cursor-pointer text-left ${
                  selectedProvider === 'custom'
                    ? 'border-brand-green bg-bg-raised'
                    : 'border-border-main bg-bg-card hover:border-border-strong hover:bg-bg-raised'
                }`}
              >
                <div className="w-11 h-11 rounded-xl bg-status-info-soft border border-status-info/20 flex items-center justify-center text-lg font-bold shrink-0 text-status-info">
                  +
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-text-main">其他 OpenAI 兼容平台</div>
                  <div className="text-[12px] text-text-muted mt-0.5 leading-relaxed">
                    硅基流动、月之暗面、通义千问等<br />
                    只要兼容 OpenAI 格式即可
                  </div>
                </div>
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                  selectedProvider === 'custom' ? 'bg-brand-green border-brand-green' : 'border-border-strong'
                }`}>
                  {selectedProvider === 'custom' && <span className="text-[10px] text-white font-bold">✓</span>}
                </div>
              </button>
            </div>

            {/* Cost note */}
            <div className="flex items-start gap-3 p-4 bg-status-warn-soft border border-status-warn/15 rounded-xl">
              <span className="text-base shrink-0">💰</span>
              <div className="text-[12px] text-text-muted leading-relaxed">
                <b className="text-text-main">费用说明</b>：摘星完全免费。AI 平台按用量计费——生成一次群摘要约 <b className="text-text-main">0.01 元</b>，每月通常不超 <b className="text-text-main">3 元</b>。DeepSeek 赠送的免费额度足够用几个月。
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-3 pt-2">
              <motion.button
                whileTap={{ scale: 0.97 }}
                onClick={() => setSubstep(1)}
                disabled={!selectedProvider}
                className={`py-2.5 px-8 rounded-full text-[14px] font-semibold transition-all cursor-pointer ${
                  selectedProvider
                    ? 'bg-brand-green-hover text-white hover:opacity-90'
                    : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
                }`}
              >
                选好了，下一步 <span className="inline-block ml-0.5">→</span>
              </motion.button>
              <button onClick={handleSkip} className="text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer">
                跳过，稍后配置
              </button>
            </div>
          </div>
        )}

        {/* ═══ Step 1: Guide ═══ */}
        {substep === 1 && (
          <div className="space-y-5">
            <h4 className="text-sm font-semibold text-text-main">
              {isCustom ? '在其他平台获取 API Key' : '去 DeepSeek 拿你的钥匙'}
            </h4>
            <p className="text-[13px] text-text-muted leading-relaxed">
              API Key 就像一串密码，告诉 AI 平台「我是合法用户」。跟着下面步骤走，1 分钟拿到。
            </p>

            {/* ── DeepSeek guide panel ── */}
            {!isCustom && (
              <div className="bg-bg-card border border-border-main rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-border-main flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-lg bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-xs font-bold shrink-0 text-brand-green">D</div>
                  <span className="text-sm font-semibold text-text-main">DeepSeek 注册 + 获取 Key</span>
                </div>
                <div className="px-5 py-2 divide-y divide-border-main/40">
                  {[
                    { num: '1', html: '打开 <b class="text-text-main">platform.deepseek.com</b>，点击右上角「注册」。用手机号注册即可。' },
                    { num: '2', html: '登录后，进入左侧菜单 <b class="text-text-main">「API Keys」</b>，点击「创建 API Key」，复制生成的 <code class="bg-bg-raised px-1.5 py-0.5 rounded text-[12px] font-mono text-brand-green">sk-xxxxxxxx</code>。' },
                    { num: '3', html: '把复制的 Key 粘贴到下一步的输入框中。<b class="text-text-main">只粘贴一次，后面不会再看</b>。' },
                  ].map(({ num, html }) => (
                    <div key={num} className="flex items-start gap-3 py-3.5">
                      <div className="w-7 h-7 rounded-full bg-brand-green-light border border-brand-green/20 flex items-center justify-center text-xs font-bold shrink-0 mt-0.5 text-brand-green">{num}</div>
                      <div className="text-[13px] text-text-muted leading-relaxed" dangerouslySetInnerHTML={{ __html: html }} />
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ── Custom platform guide ── */}
            {isCustom && (
              <div className="bg-bg-card border border-border-main rounded-2xl overflow-hidden">
                <div className="px-5 py-3.5 border-b border-border-main flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-lg bg-status-info-soft border border-status-info/20 flex items-center justify-center text-xs font-bold shrink-0 text-status-info">+</div>
                  <span className="text-sm font-semibold text-text-main">其他兼容平台</span>
                </div>
                <div className="px-5 py-2 divide-y divide-border-main/40">
                  {[
                    { num: '1', html: '在你使用的 AI 平台上注册并获取 <b class="text-text-main">API Key</b>。平台需支持 OpenAI 兼容的 Chat Completions 接口。' },
                    { num: '2', html: '记录两个信息：<b class="text-text-main">API 站点地址</b>和 <b class="text-text-main">API Key</b>。站点地址格式如 <code class="bg-bg-raised px-1.5 py-0.5 rounded text-[12px] font-mono text-brand-green">https://api.example.com</code>。' },
                    { num: '3', html: '在下一步中分别填入站点地址和 Key，然后点击「检测连通」。' },
                  ].map(({ num, html }) => (
                    <div key={num} className="flex items-start gap-3 py-3.5">
                      <div className="w-7 h-7 rounded-full bg-status-info-soft border border-status-info/20 flex items-center justify-center text-xs font-bold shrink-0 mt-0.5 text-status-info">{num}</div>
                      <div className="text-[13px] text-text-muted leading-relaxed" dangerouslySetInnerHTML={{ __html: html }} />
                    </div>
                  ))}
                </div>
              </div>
            )}


            {/* Actions */}
            <div className="flex items-center gap-3">
              <button onClick={() => setSubstep(0)} className="py-2.5 px-6 rounded-full text-[13px] font-medium text-text-muted border border-border-main hover:text-text-main transition-colors cursor-pointer">
                ← 返回
              </button>
              <motion.button
                whileTap={{ scale: 0.97 }}
                onClick={() => setSubstep(2)}
                className="py-2.5 px-8 rounded-full text-[14px] font-semibold bg-brand-green-hover text-white hover:opacity-90 transition-all cursor-pointer"
              >
                拿到 Key 了，下一步 <span className="inline-block ml-0.5">→</span>
              </motion.button>
            </div>
            <button onClick={handleSkip} className="text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer">
              跳过，稍后配置
            </button>
          </div>
        )}

        {/* ═══ Step 2: Fill & Detect ═══ */}
        {substep === 2 && (
          <div className="space-y-5">
            <h4 className="text-sm font-semibold text-text-main">把 Key 粘贴进来，我们帮你验证</h4>
            <p className="text-[13px] text-text-muted leading-relaxed">
              填入刚才复制的 API Key，点击「检测连通」——成功就代表 AI 已经准备好了。
            </p>

            <div className="bg-bg-card border border-border-main rounded-2xl p-5 space-y-4">
              {/* Base URL for custom */}
              {isCustom && (
                <Field label="API 站点地址" hint="填写平台提供的 API 地址，不要以斜杠结尾">
                  <Input
                    value={customBaseUrl}
                    onChange={v => { setCustomBaseUrl(v); setDetectResult(null) }}
                    placeholder="https://api.example.com"
                  />
                </Field>
              )}

              {/* API Key */}
              <Field label="API Key" hint="以 sk- 开头的一串字符，粘贴即可">
                <Input
                  type="password"
                  value={apiKey}
                  onChange={v => { setApiKey(v); setDetectResult(null) }}
                  placeholder="sk-xxxxxxxxxxxxxxxx"
                />
              </Field>

              {/* Detect button */}
              <div className="flex items-center">
                <motion.button
                  whileTap={{ scale: 0.97 }}
                  onClick={handleDetect}
                  disabled={detecting || !apiKey.trim() || (isCustom && !customBaseUrl.trim())}
                  className={`py-2.5 px-6 rounded-full text-[14px] font-semibold transition-all cursor-pointer flex items-center gap-2 border ${
                    detecting || !apiKey.trim() || (isCustom && !customBaseUrl.trim())
                      ? 'bg-bg-raised text-text-muted border-border-main cursor-not-allowed'
                      : 'bg-brand-green-light border-brand-green/20 text-brand-green hover:bg-brand-green/15'
                  }`}
                >
                  {detecting ? (
                    <><CircleNotch size={16} className="animate-spin" /> 检测中...</>
                  ) : (
                    <><MagnifyingGlass size={16} /> 检测连通</>
                  )}
                </motion.button>
              </div>

              {/* Detect result */}
              {detectResult && (
                <div className={`p-4 rounded-xl border ${
                  detectSuccess
                    ? 'bg-brand-green-light border-brand-green/20'
                    : 'bg-status-error-soft border-status-error/20'
                }`}>
                  {detectSuccess ? (
                    <div className="flex items-start gap-2.5">
                      <CheckCircle size={18} weight="fill" className="text-brand-green shrink-0 mt-0.5" />
                      <div className="flex-1">
                        <p className="font-semibold text-brand-green">✓ 连通成功！</p>
                        {hasModels && (
                          <div className="mt-2">
                            <p className="text-[12px] text-text-muted mb-1.5">选择模型：</p>
                            <div className="flex flex-wrap gap-1.5">
                              {detectResult.available_models.map(m => (
                                <button
                                  key={m}
                                  onClick={() => { setSelectedModel(m); updateData({ ai_provider_model: m }) }}
                                  className={`px-3 py-1 rounded-full text-[12px] font-mono cursor-pointer transition-colors border ${
                                    selectedModel === m
                                      ? 'bg-brand-green-light text-brand-green border-brand-green/20 font-semibold'
                                      : 'bg-bg-raised text-text-muted border-border-main hover:border-text-muted/30'
                                  }`}
                                >
                                  {m}
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start gap-2.5">
                      <Warning size={18} weight="fill" className="text-status-error shrink-0 mt-0.5" />
                      <div>
                        <p className="font-semibold text-status-error">连通失败</p>
                        <p className="text-[13px] text-text-muted mt-1">{detectResult.error || '请检查 Key 和站点地址是否正确'}</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="flex items-center gap-3">
              <button onClick={() => setSubstep(1)} className="py-2.5 px-6 rounded-full text-[13px] font-medium text-text-muted border border-border-main hover:text-text-main transition-colors cursor-pointer">
                ← 返回
              </button>
              <motion.button
                whileTap={{ scale: 0.97 }}
                onClick={handleFinish}
                disabled={!canFinish}
                className={`py-2.5 px-8 rounded-full text-[14px] font-semibold transition-all cursor-pointer flex items-center gap-2 ${
                  canFinish
                    ? 'bg-brand-green-hover text-white hover:opacity-90'
                    : 'bg-bg-raised text-text-muted border border-border-main cursor-not-allowed'
                }`}
              >
                {detecting ? (
                  <Spinner size={18} weight="bold" className="animate-spin" />
                ) : (
                  <><CheckCircle size={18} /> 完成配置</>
                )}
              </motion.button>
            </div>
            <button onClick={handleSkip} className="text-xs text-text-muted hover:text-text-main transition-colors cursor-pointer">
              跳过，稍后配置
            </button>
          </div>
        )}
      </motion.div>
    </div>
  )
}

// ── Step 4: Feature Overview ──────────────────────────────────────────

const CAPABILITY_CARDS = [
  {
    icon: '💬',
    title: '会话管理',
    desc: '浏览聊天记录，AI 对话查询聊天记录，聊天气泡富渲染，聊天导出归档。',
  },
  {
    icon: '🤖',
    title: '智能助手',
    desc: '群聊关键词提醒 + 定时 AI 摘要 + 公众号即时提醒。每个群独立配置，摘要可推送通知。',
  },
  {
    icon: '⭐',
    title: '收藏 & 朋友圈',
    desc: '收藏按类型/标签/关键词筛选导出；朋友圈浏览、图片灯箱、视频下载、HTML 归档。',
  },
  {
    icon: '📊',
    title: '运行状态',
    desc: '实时日志流、AI 调试台、系统配置中心。启停 Bot，查看消息统计和 AI 调用延迟。',
  },
]

export function Step4Features({ data, updateData, onComplete }) {
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  async function handleFinish() {
    setBusy(true)
    try {
      await fetch(`${API}/api/onboarding/step4`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
    } catch {}
    setDone(true)
    setTimeout(onComplete, 1200)
    setBusy(false)
  }

  if (done) {
    return (
      <motion.div initial={{opacity:0,scale:0.95}} animate={{opacity:1,scale:1}} transition={spring}
        className="flex flex-col items-center justify-center py-16">
        <motion.div initial={{scale:0}} animate={{scale:1}} transition={{delay:0.1,...spring}}
          className="w-20 h-20 rounded-full bg-brand-green/10 border border-brand-green/20 flex items-center justify-center mb-6 shadow-sm">
          <CheckCircle size={38} weight="fill" className="text-brand-green" />
        </motion.div>
        <h2 className="text-lg font-bold text-text-main mb-2">配置就绪</h2>
        <p className="text-sm text-text-muted font-medium">正在启动夜航控制台仪表盘...</p>
      </motion.div>
    )
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <div className="w-1.5 h-4.5 rounded-full bg-brand-green" />
        <h3 className="text-base font-semibold tracking-tight text-text-main">功能概览</h3>
      </div>

      <p className="text-sm text-text-muted mb-5 leading-relaxed">
        微信助手的核心能力一览，所有功能无需额外配置，进入主界面即可使用。
      </p>

      <div className="grid grid-cols-2 gap-4">
        {CAPABILITY_CARDS.map(card => (
          <div key={card.title} className="bg-bg-raised border border-border-main rounded-2xl p-5">
            <div className="text-xl mb-3">{card.icon}</div>
            <div className="text-sm font-semibold text-text-main mb-1.5">{card.title}</div>
            <div className="text-xs text-text-muted leading-relaxed">{card.desc}</div>
          </div>
        ))}
      </div>

      <div className="mt-8 pt-4">
        <motion.button
          whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }}
          onClick={handleFinish}
          disabled={busy}
          className="w-56 py-2.5 rounded-full text-[14px] font-semibold tracking-wide transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer bg-brand-green-hover text-white hover:opacity-90 animate-pulse"
        >
          {busy ? <Spinner size={18} weight="bold" className="animate-spin" /> : <><CheckCircle size={18} /> 开始使用</>}
        </motion.button>
      </div>
    </div>
  )
}
