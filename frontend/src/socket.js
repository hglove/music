export function createSocket(onEvent) {
  let ws = null
  let closed = false
  let delay = 500

  const connect = () => {
    if (closed) return
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    ws = new WebSocket(`${proto}//${location.host}/ws`)
    ws.onopen = () => { delay = 500 }
    ws.onmessage = (ev) => {
      try {
        onEvent(JSON.parse(ev.data))
      } catch {
        /* ignore malformed frames */
      }
    }
    ws.onclose = () => {
      ws = null
      if (!closed) {
        setTimeout(connect, delay)
        delay = Math.min(delay * 1.6, 5000)
      }
    }
    ws.onerror = () => {
      try { ws?.close() } catch { /* noop */ }
    }
  }

  connect()

  return {
    send(msg) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(typeof msg === 'string' ? msg : JSON.stringify(msg))
      }
    },
    close() {
      closed = true
      ws?.close()
    },
  }
}
