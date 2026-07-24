export interface FeaturedBook {
  title: string
  cover: string
}

export const featuredBooks: FeaturedBook[] = [
  { title: '老人と海', cover: '/figma-books/old-man-and-sea.png' },
  { title: '書影でたどる関西の出版100', cover: '/figma-books/publishing-kansai.png' },
  { title: '小さな故意の物語', cover: '/figma-books/small-intent.png' },
]

export const figmaResultBooks: FeaturedBook[] = [
  { title: 'アルジャーノンに花束を', cover: '/figma-books/flowers-for-algernon.png' },
  { title: '老人と海', cover: '/figma-books/old-man-and-sea.png' },
  { title: '人間失格', cover: '/figma-books/no-longer-human.png' },
  { title: '夏への扉', cover: '/figma-books/door-into-summer.png' },
  { title: '幼年期の終り', cover: '/figma-books/childhoods-end.png' },
  { title: '1984', cover: '/figma-books/nineteen-eighty-four.png' },
]

export function fallbackCoverForTitle(title: string): string | undefined {
  return [...figmaResultBooks, ...featuredBooks].find(book => title.includes(book.title) || book.title.includes(title))?.cover
}
