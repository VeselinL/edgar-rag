import avatarUrl from '../../avatar/ava.png'

interface Props {
  size?: 'small' | 'message' | 'large'
  decorative?: boolean
}

export function AvaAvatar({
  size = 'message',
  decorative = false,
}: Props) {
  return (
    <div className={`avatar avatar--${size}`}>
      <img
        src={avatarUrl}
        alt={decorative ? '' : 'AVA'}
      />
    </div>
  )
}
