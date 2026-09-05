import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConversationSidebar } from './ConversationSidebar'

const conversations = Array.from({ length: 8 }, (_, index) => ({
  id: `chat-${index + 1}`,
  title: `Chat ${index + 1}`,
  memory_enabled: false,
  pinned: false,
  pinned_at: null,
  created_at: '2026-09-05T00:00:00Z',
  updated_at: '2026-09-05T00:00:00Z',
  company_scope: [],
}))

function renderSidebar() {
  return render(
    <ConversationSidebar
      conversations={conversations}
      language="en"
      companyScope={['TSLA', 'F', 'GM', 'NVDA']}
      onNew={() => {}}
      onToggleCompany={() => {}}
      onSelect={() => {}}
      onPin={() => {}}
      onRename={() => {}}
      onDelete={() => {}}
      onDeleteAll={() => {}}
      onExport={() => {}}
    />,
  )
}

describe('ConversationSidebar', () => {
  it('compresses company scope and shows seven recent chats until expanded', async () => {
    renderSidebar()

    expect(screen.getByText('Tesla, Ford, General Motors…')).toBeInTheDocument()
    expect(screen.queryByLabelText('All companies')).not.toBeInTheDocument()
    expect(screen.getByText('Chat 7')).toBeInTheDocument()
    expect(screen.queryByText('Chat 8')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Show more chats' }))
    expect(screen.getByText('Chat 8')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Show fewer chats' }))
    expect(screen.queryByText('Chat 8')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Company scope' }))
    expect(screen.getByLabelText('All companies')).toBeInTheDocument()
  })
})
