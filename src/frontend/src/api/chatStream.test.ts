import { streamChat } from './chatStream'

function streamedResponse(chunks: string[]) {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('streamChat', () => {
  afterEach(() => vi.restoreAllMocks())

  it('parses split SSE frames and preserves fragment whitespace', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamedResponse([
      'event: delta\ndata: {"text":"First "}\n',
      '\nevent: delta\ndata: {"text":" part"}\n\n',
      'event: sources\ndata: {"sources":[],"source_status":"none_cited","malformed_source_count":0}\n\nevent: done\ndata: {}\n\n',
    ]))
    const fragments: string[] = []
    const eventOrder: string[] = []
    await streamChat('Original query', {
      signal: new AbortController().signal,
      onOpen: () => eventOrder.push('open'),
      onDelta: (text) => { fragments.push(text); eventOrder.push('delta') },
      onSources: () => eventOrder.push('sources'),
      onDone: () => eventOrder.push('done'),
    })
    expect(fragments.join('')).toBe('First  part')
    expect(eventOrder).toEqual(['open', 'delta', 'delta', 'sources', 'done'])
    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/chat/stream',
      expect.objectContaining({ body: JSON.stringify({ query: 'Original query' }) }),
    )
  })

  it('rejects a missing or unknown source status', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamedResponse([
      'event: sources\ndata: {"sources":[]}\n\n',
    ]))
    await expect(streamChat('Question', {
      signal: new AbortController().signal,
      onOpen: vi.fn(),
      onDelta: vi.fn(),
      onSources: vi.fn(),
      onDone: vi.fn(),
    })).rejects.toThrow('invalid source status')
  })

  it('ignores empty delta text', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamedResponse([
      'event: delta\ndata: {"text":""}\n\n',
      'event: done\ndata: {}\n\n',
    ]))
    const onDelta = vi.fn()
    await streamChat('Question', {
      signal: new AbortController().signal,
      onOpen: vi.fn(),
      onDelta,
      onSources: vi.fn(),
      onDone: vi.fn(),
    })
    expect(onDelta).not.toHaveBeenCalled()
  })

  it('uses a safe error-state fallback when an error event has no message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamedResponse([
      'event: error\ndata: {}\n\n',
    ]))

    await expect(streamChat('Question', {
      signal: new AbortController().signal,
      onOpen: vi.fn(),
      onDelta: vi.fn(),
      onSources: vi.fn(),
      onDone: vi.fn(),
    })).rejects.toThrow('unusable error response')
  })
})
