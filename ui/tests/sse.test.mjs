import assert from 'node:assert/strict'
import test from 'node:test'
import { readSSE } from '../src/utils/sse.mjs'

const collect = async (pieces) => {
  const body = new ReadableStream({
    start(controller) {
      pieces.forEach(piece => controller.enqueue(piece))
      controller.close()
    },
  })
  return Array.fromAsync(readSSE(body))
}
const encoder = new TextEncoder()

test('event type and UTF-8 survive every possible network split', async () => {
  const wire = encoder.encode('event: token\ndata: {"content":"中文回复"}\n\nevent: done\ndata: {}\n\n')
  const expected = [{ event: 'token', data: '{"content":"中文回复"}' }, { event: 'done', data: '{}' }]
  for (let i = 1; i < wire.length; i++) {
    assert.deepEqual(await collect([wire.slice(0, i), wire.slice(i)]), expected)
  }
  assert.deepEqual(await collect(Array.from(wire, byte => new Uint8Array([byte]))), expected)
})

test('CRLF, comments, multiline data and final unterminated event', async () => {
  const wire = ': heartbeat\r\nevent:error\r\ndata: {\r\ndata: "message":"失败"}\r\n\r\nevent: done\ndata: {}'
  assert.deepEqual(await collect([encoder.encode(wire)]), [
    { event: 'error', data: '{\n"message":"失败"}' }, { event: 'done', data: '{}' },
  ])
})

test('terminal-event consumer cancels keep-alive body and releases the lock', async () => {
  let cancelled = false
  const body = new ReadableStream({
    start(controller) { controller.enqueue(encoder.encode('event: done\ndata: {}\n\n')) },
    cancel() { cancelled = true },
  })
  for await (const event of readSSE(body)) {
    assert.equal(event.event, 'done')
    break
  }
  assert.equal(cancelled, true)
  assert.equal(body.locked, false)
})
