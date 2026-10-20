// SSE events may span any number of network reads, including between the
// event and data lines or within a UTF-8 character.
export async function* readSSE(body) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let event = 'message'
  let data = []
  const parseLine = (line) => {
    if (line === '') {
      const result = data.length ? { event, data: data.join('\n') } : null
      event = 'message'
      data = []
      return result
    }
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    if (field === 'data') data.push(value)
    return null
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let newline
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, '')
        buffer = buffer.slice(newline + 1)
        const parsed = parseLine(line)
        if (parsed) yield parsed
      }
      if (done) {
        if (buffer) parseLine(buffer.replace(/\r$/, ''))
        const parsed = parseLine('')
        if (parsed) yield parsed
        return
      }
    }
  } finally {
    try { await reader.cancel() } catch {}
    reader.releaseLock()
  }
}
