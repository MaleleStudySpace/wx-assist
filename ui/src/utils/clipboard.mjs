export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return
  } catch {
    // WebView or HTTP LAN access may lack the Clipboard API/permission.
  }
  const activeElement = document.activeElement
  const selection = window.getSelection()
  const ranges = Array.from({ length: selection?.rangeCount || 0 }, (_, i) => selection.getRangeAt(i))
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.cssText = 'position:fixed;left:-9999px;top:0'
  document.body.appendChild(textarea)
  try {
    textarea.select()
    if (!document.execCommand('copy')) throw new Error('复制失败，请选择文字手动复制')
  } finally {
    textarea.remove()
    activeElement?.focus({ preventScroll: true })
    if (selection) {
      selection.removeAllRanges()
      ranges.forEach(range => selection.addRange(range))
    }
  }
}
