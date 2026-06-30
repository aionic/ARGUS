"use client"

import * as React from "react"

/** Compact document-update event pushed from the backend SSE stream. */
export interface DocumentEvent {
  id: string
  dataset?: string
  blob_name?: string
  ocr_completed?: boolean
  processing_completed?: boolean
  extraction_backend_used?: string
  flagged?: boolean
  ts?: number
}

export type LiveStatus = "connecting" | "open" | "closed"

/**
 * Subscribe to the backend Server-Sent Events stream for live document updates.
 *
 * Opens a single EventSource to the Next.js proxy (`/api/backend/api/events`),
 * which injects the backend API key server-side. The browser EventSource cannot
 * set custom headers, so this proxy hop is what makes authenticated SSE work.
 *
 * `onEvent` is invoked for every `document` event. The latest callback is always
 * used (kept in a ref) so consumers don't need to memoize it, and the connection
 * is established once per mount. The browser auto-reconnects on transient errors;
 * a hard close triggers a manual reconnect after a short backoff.
 */
export function useDocumentEvents(onEvent: (event: DocumentEvent) => void): LiveStatus {
  const [status, setStatus] = React.useState<LiveStatus>("connecting")
  const onEventRef = React.useRef(onEvent)
  onEventRef.current = onEvent

  React.useEffect(() => {
    let source: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let closedByUs = false

    function connect() {
      source = new EventSource("/api/backend/api/events")

      source.onopen = () => setStatus("open")

      source.addEventListener("ready", () => setStatus("open"))

      source.addEventListener("document", (ev) => {
        try {
          const data = JSON.parse((ev as MessageEvent).data) as DocumentEvent
          onEventRef.current(data)
        } catch {
          // Ignore malformed payloads.
        }
      })

      source.onerror = () => {
        // EventSource will auto-reconnect while readyState is CONNECTING.
        if (source && source.readyState === EventSource.CLOSED && !closedByUs) {
          setStatus("connecting")
          source.close()
          source = null
          reconnectTimer = setTimeout(connect, 3000)
        } else if (!closedByUs) {
          setStatus("connecting")
        }
      }
    }

    connect()

    return () => {
      closedByUs = true
      setStatus("closed")
      if (reconnectTimer) clearTimeout(reconnectTimer)
      source?.close()
    }
  }, [])

  return status
}
