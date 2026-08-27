import darkAvatarUrl from '../../avatar/ava-dark.png'
import lightAvatarUrl from '../../avatar/ava-light.png'
import type { Theme } from '../hooks/useTheme'

interface Props {
  size?: 'small' | 'message' | 'large'
  decorative?: boolean
  theme: Theme
}

export function AvaAvatar({
  size = 'message',
  decorative = false,
  theme,
}: Props) {
  return (
    <div className={`avatar avatar--${size}`}>
      <img
        src={theme === 'dark' ? darkAvatarUrl : lightAvatarUrl}
        alt={decorative ? '' : 'AVA'}
      />
    </div>
  )
}
