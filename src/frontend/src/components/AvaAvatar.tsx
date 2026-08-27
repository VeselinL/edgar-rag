interface Props {
  size?: 'small' | 'message' | 'large'
  decorative?: boolean
}

import lightAvatarUrl from '../../avatar/ava-light.png'
import darkAvatarUrl from '../../avatar/ava-dark.png'

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
        className="avatar__light"
        src={lightAvatarUrl}
        alt={decorative ? '' : 'AVA'}
      />
      <img
        className="avatar__dark"
        src={darkAvatarUrl}
        alt={decorative ? '' : 'AVA'}
      />
    </div>
  )
}
