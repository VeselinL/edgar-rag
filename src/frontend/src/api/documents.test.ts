import { deleteDocument, DocumentRequestError, listDocuments, uploadDocument } from './documents'

describe('document API', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    document.cookie = 'ava_csrf=upload-csrf; path=/'
  })

  it('uploads the original bytes with the bounded media type and CSRF token', async () => {
    const documentRecord = {
      id: 'document-1', conversation_id: 'conversation-1', filename: 'notes.txt',
      media_type: 'text/plain', size_bytes: 5, status: 'ready', page_count: null,
      token_count: 1, chunk_count: 1, created_at: '2026-09-01T00:00:00Z',
      updated_at: '2026-09-01T00:00:00Z',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(documentRecord), { status: 201, headers: { 'Content-Type': 'application/json' } }),
    )
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })

    await expect(uploadDocument('conversation-1', file)).resolves.toEqual(documentRecord)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/conversations/conversation-1/documents?filename=notes.txt'),
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: file,
        headers: expect.objectContaining({ 'Content-Type': 'text/plain', 'X-CSRF-Token': 'upload-csrf' }),
      }),
    )
  })

  it('rejects unsupported files before a network request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    await expect(uploadDocument('conversation-1', new File(['x'], 'script.html', { type: 'text/html' })))
      .rejects.toBeInstanceOf(DocumentRequestError)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('lists and deletes exact chat-owned documents', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ documents: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await expect(listDocuments('chat/a')).resolves.toEqual([])
    await expect(deleteDocument('chat/a', 'document/b')).resolves.toBeUndefined()
    expect(fetchMock.mock.calls[0][0]).toContain('/api/conversations/chat%2Fa/documents')
    expect(fetchMock.mock.calls[1][0]).toContain('/documents/document%2Fb')
  })
})
