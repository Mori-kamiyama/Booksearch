import { test, expect } from '@playwright/test'
const books = [{ id: 1, title: '夏目漱石作品集', authors: '夏目 漱石', shelf_candidates: [] }, { id: 2, title: 'こころ', authors: '夏目 漱石', shelf_candidates: [] }]
const index = { version: 1, books, topics: [{ id: 'ndc-0', label: '総記・情報', aliases: ['総記・情報'], count: 4 }, { id: 'ndc-7', label: '芸術', aliases: ['芸術'], count: 2 }, { id: 'c-language', label: 'C言語', aliases: ['C言語'], count: 3, genres: ['ndc-0'] }], coverage: { books: 2, page_count: 0, level: 0 } }
test.beforeEach(async ({ page }) => {
  await page.route('**/search-index.json', route => route.fulfill({ json: index }))
  await page.route('**/api/**', route => route.fulfill({ json: { books, total: books.length } }))
})
test('local suggestions select author and title without per-keystroke API calls', async ({ page }) => {
  const requests: string[] = []
  page.on('request', request => { if (request.url().includes('/api/books/search')) requests.push(request.url()) })
  await page.goto('/')
  const input = page.getByRole('combobox', { name: '本を検索' })
  await input.fill('夏目')
  await expect(page.getByRole('option')).toHaveCount(2)
  expect(requests).toHaveLength(0)
  await input.press('ArrowDown')
  await input.press('Enter')
  await expect(page.getByRole('heading', { name: '検索結果' })).toBeVisible()
  await input.fill('ここ')
  await page.getByRole('option', { name: 'こころ 書名' }).click()
  await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe('こころ')
})
test('topic selection opens a filtered search without requiring a title query', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('combobox', { name: '本を検索' }).fill('Ｃ言語')
  await page.getByRole('option', { name: 'C言語 テーマ' }).click()
  await expect(page).toHaveURL(/topic=c-language/)
  await expect(page.getByRole('button', { name: 'C言語を解除' })).toBeVisible()
  await expect(page.getByText('2件ヒット')).toBeVisible()
})
test('composition and Escape preserve ordinary search', async ({ page }) => {
  await page.goto('/')
  const input = page.getByRole('combobox', { name: '本を検索' })
  await input.focus()
  await input.dispatchEvent('compositionstart')
  await input.fill('夏目')
  await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
  await expect(input).toHaveAttribute('aria-expanded', 'false')
  await input.dispatchEvent('compositionend')
  await expect(page.getByRole('listbox', { name: '検索候補' })).toBeVisible()
  await input.press('Escape')
  await input.press('Enter')
  await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe('夏目')
})
test('late index uses the latest input and failed index never blocks submit', async ({ page }) => {
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/search-index.json', async route => { await gate; await route.fulfill({ json: index }) })
  await page.goto('/')
  const input = page.getByRole('combobox', { name: '本を検索' })
  const requested = page.waitForRequest('**/search-index.json')
  await input.fill('夏目')
  await requested
  await input.fill('ここ')
  release()
  await expect(page.getByRole('option')).toHaveCount(1)
  await expect(page.getByRole('option')).toContainText('こころ')
  await page.route('**/search-index.json', route => route.fulfill({ status: 503 }))
  await page.reload()
  await input.fill('検索')
  await input.press('Enter')
  await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe('検索')
})
test('touch selects a candidate', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ hasTouch: true, viewport: { width: 390, height: 844 } })
  const page = await context.newPage()
  await page.route('**/search-index.json', route => route.fulfill({ json: index }))
  await page.route('**/api/**', route => route.fulfill({ json: { books, total: 2 } }))
  await page.goto(baseURL!)
  await page.getByRole('combobox', { name: '本を検索' }).fill('ここ')
  await page.getByRole('option', { name: 'こころ 書名' }).tap()
  await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe('こころ')
  await context.close()
})
test('filters preserve URL on pagination and back from detail', async ({ page }) => {
  await page.route('**/api/books/search?**', route => route.fulfill({ json: { books, total: 65 } }))
  await page.goto('/search?q=本&topic=c-language&author=田中')
  await expect.poll(() => new URL(page.url()).searchParams.has('author')).toBe(false)
  await expect(page.getByLabel('著者', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '次のページ' }).click()
  await expect(page).toHaveURL(/page=2/)
  expect(new URL(page.url()).searchParams.get('topic')).toBe('c-language')
  await page.getByRole('button', { name: 'こころ', exact: true }).click()
  expect(new URL(page.url()).searchParams.has('author')).toBe(false)
  await page.goBack()
  await expect(page.getByRole('button', { name: 'C言語を解除' })).toBeVisible()
  await page.getByRole('button', { name: 'C言語を解除' }).click()
  await expect(page).toHaveURL(/q=/)
  expect(new URL(page.url()).searchParams.has('topic')).toBe(false)
})

for (const width of [320, 1024]) {
  test(`filter panel has draft state, genre/theme separation and dismiss at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 812 })
    await page.goto('/search?q=本')
    const trigger = page.getByRole('button', { name: 'フィルター', exact: true })
    await expect(page.getByRole('dialog')).not.toBeVisible()
    await trigger.click()
    const panel = page.getByRole('dialog', { name: 'フィルター' })
    await expect(panel).toBeVisible()
    await expect(panel.getByLabel('著者')).toHaveCount(0)
    await panel.getByRole('combobox', { name: 'ジャンル', exact: true }).selectOption('ndc-0')
    await panel.getByRole('button', { name: 'C言語', exact: true }).click()
    expect(new URL(page.url()).searchParams.has('topic')).toBe(false)
    const rect = await panel.boundingBox()
    expect(rect!.x).toBeGreaterThanOrEqual(0)
    expect(rect!.x + rect!.width).toBeLessThanOrEqual(width)
    await page.keyboard.press('Escape')
    await expect(panel).not.toBeVisible()
    await expect(trigger).toBeFocused()
    await trigger.click()
    await expect(panel.getByRole('combobox', { name: 'ジャンル', exact: true })).toHaveValue('')
    await panel.getByRole('combobox', { name: 'ジャンル', exact: true }).selectOption('ndc-0')
    await panel.getByRole('button', { name: 'C言語', exact: true }).click()
    await panel.getByRole('button', { name: '適用する' }).click()
    await expect.poll(() => new URL(page.url()).searchParams.get('genre')).toBe('ndc-0')
    expect(new URL(page.url()).searchParams.get('topic')).toBe('c-language')
    await page.getByRole('button', { name: /フィルター/ }).click()
    await panel.getByRole('combobox', { name: 'ジャンル', exact: true }).selectOption('ndc-7')
    await expect(panel.getByRole('button', { name: 'C言語', exact: true })).toHaveCount(0)
    await expect(panel.getByRole('button', { name: '指定なし' })).toHaveAttribute('aria-pressed', 'true')
    await page.mouse.click(2, 2)
    await expect(panel).not.toBeVisible()
    expect(new URL(page.url()).searchParams.get('genre')).toBe('ndc-0')
  })
}

test('reader genres replace NDC labels and correction preserves hard filters', async ({ page }) => {
  const modernIndex = { ...index, books: [{id:1,title:'Python入門',authors:'著者',title_reading:'パイソンニュウモン'}],
    topics: [
      {id:'ndc-0',kind:'legacy',label:'総記・情報',aliases:[],count:1},
      {id:'genre-it',kind:'genre',label:'IT・プログラミング',aliases:['IT'],count:1},
      {id:'genre-design',kind:'genre',label:'デザイン・アート',aliases:['デザイン'],count:1},
      {id:'python',kind:'theme',label:'Python',aliases:['パイソン'],count:1,genres:['genre-it']},
    ],coverage:{books:1,page_count:1,level:0} }
  await page.route('**/search-index.json', route => route.fulfill({json:modernIndex}))
  await page.route('**/api/books/search?**', route => {
    const params = new URL(route.request().url()).searchParams
    return route.fulfill({json:params.get('q')==='Pythn' ? {books:[],total:0} : {books,total:2}})
  })
  await page.goto('/search?q=Pythn&genre=genre-it&max_pages=300')
  await expect(page.getByRole('status')).toContainText('次の検索結果を表示しています：Python')
  await expect(page.getByText('見つかりませんでした',{exact:true})).toHaveCount(0)
  expect(new URL(page.url()).searchParams.get('q')).toBe('Pythn')
  expect(new URL(page.url()).searchParams.get('genre')).toBe('genre-it')
  expect(new URL(page.url()).searchParams.get('max_pages')).toBe('300')
  await page.getByRole('button',{name:/フィルター/}).click()
  const panel=page.getByRole('dialog',{name:'フィルター'})
  const select=panel.getByRole('combobox',{name:'ジャンル',exact:true})
  await expect(select.locator('option')).toHaveText(['すべてのジャンル','IT・プログラミング','デザイン・アート'])
  await expect(panel.getByRole('button',{name:'Python',exact:true})).toBeVisible()
  await select.selectOption('genre-design')
  await expect(panel.getByRole('button',{name:'Python',exact:true})).toHaveCount(0)
})


test('automatic correction supports pagination and original-query opt out', async ({page}) => {
  await page.route('**/search-index.json',route=>route.fulfill({json:{...index,topics:[{id:'python',label:'Python',aliases:['パイソン'],count:1}]}}))
  const calls: URL[]=[]
  await page.route('**/api/books/search?**',route=>{
    const url=new URL(route.request().url()); calls.push(url)
    return route.fulfill({json:url.searchParams.get('q')==='Python'?{books,total:65}:{books:[],total:0}})
  })
  await page.goto('/search?q=Pthon&max_pages=300')
  await expect(page.getByRole('status')).toContainText('Python')
  await page.getByRole('button',{name:'次のページ'}).click()
  await expect(page).toHaveURL(/page=2/)
  await expect(page.getByRole('status')).toContainText('Python')
  await expect.poll(()=>calls.some(u=>u.searchParams.get('q')==='Python' && u.searchParams.get('offset')==='30')).toBe(true)
  expect(calls.every(u=>u.searchParams.get('max_pages')==='300')).toBe(true)
  await page.getByRole('button',{name:'元の検索語「Pthon」で検索'}).click()
  await expect(page).toHaveURL(/exact=1/)
  await expect(page.getByText('見つかりませんでした',{exact:true})).toBeVisible()
  await expect(page.getByText('次の検索結果を表示しています：',{exact:false})).toHaveCount(0)
  const before=calls.filter(u=>u.searchParams.get('q')==='Python').length
  await page.reload()
  await expect(page.getByText('見つかりませんでした',{exact:true})).toBeVisible()
  expect(calls.filter(u=>u.searchParams.get('q')==='Python')).toHaveLength(before)
})

test('nonsense has no automatic search and zero filtered correction stays empty',async({page})=>{
  const queries:string[]=[]
  await page.route('**/search-index.json',route=>route.fulfill({json:{...index,topics:[{id:'python',label:'Python',aliases:[],count:1}]}}))
  await page.route('**/api/books/search?**',route=>{queries.push(new URL(route.request().url()).searchParams.get('q')!);return route.fulfill({json:{books:[],total:0}})})
  await page.goto('/search?q=zzzzqqqqvvvv')
  await expect(page.getByText('見つかりませんでした',{exact:true})).toBeVisible()
  expect(queries.every(q=>q==='zzzzqqqqvvvv')).toBe(true)
  await page.goto('/search?q=Pthon&max_pages=1')
  await expect(page.getByText('見つかりませんでした',{exact:true})).toBeVisible()
  expect(queries).toContain('Python')
  await expect(page.getByText('次の検索結果を表示しています：',{exact:false})).toHaveCount(0)
})

for(const width of [320,390,1024]) test(`themes expand inside a stable filter panel at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:844})
  await page.route('**/search-index.json',route=>route.fulfill({json:{...index,
    topics:Array.from({length:24},(_,i)=>({id:`theme-${i}`,kind:'theme',label:`テーマ${i}`,aliases:[],count:1})),
    coverage:{books:2,page_count:2,level:1}}}))
  await page.goto('/search?q=本&topic=theme-20')
  await page.getByRole('button',{name:/フィルター/}).click()
  const panel=page.getByRole('dialog',{name:'フィルター'})
  await expect(panel.getByRole('button',{name:'テーマ20',exact:true})).toBeVisible()
  await expect(panel.getByRole('button',{name:'テーマ19',exact:true})).toHaveCount(0)
  await expect(panel.getByText('ページ数・レベル',{exact:true})).toBeInViewport()
  const footer=panel.getByRole('button',{name:'適用する'})
  await panel.getByRole('button',{name:/もっと表示する/}).click()
  await expect(panel.getByRole('button',{name:'テーマ19',exact:true})).toHaveCount(1)
  await expect(footer).toBeInViewport()
  const dimensions=await panel.evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth,overflow:getComputedStyle(el).overflowY}))
  expect(dimensions.scroll).toBe(dimensions.client)
  expect(dimensions.overflow).toBe('hidden')
  await panel.getByRole('button',{name:'表示を減らす'}).click()
  await expect(panel.getByRole('button',{name:'テーマ20',exact:true})).toBeVisible()
  await panel.getByText('ページ数・レベル',{exact:true}).click()
  await panel.getByLabel('ページ数（上限）').fill('250')
  await footer.click()
  expect(new URL(page.url()).searchParams.get('max_pages')).toBe('250')
  expect(new URL(page.url()).searchParams.get('topic')).toBe('theme-20')
})

test('late automatic correction cannot replace a newer search', async({page})=>{
  let release:()=>void=()=>{}
  const gate=new Promise<void>(resolve=>{release=resolve})
  await page.route('**/search-index.json',route=>route.fulfill({json:{...index,topics:[{id:'python',label:'Python',aliases:[],count:1}]}}))
  await page.route('**/api/books/search?**',async route=>{
    const q=new URL(route.request().url()).searchParams.get('q')
    if(q==='Python') await gate
    await route.fulfill({json:q==='Pthon'?{books:[],total:0}:{books,total:2}})
  })
  const started=page.waitForRequest(r=>new URL(r.url()).searchParams.get('q')==='Python')
  await page.goto('/search?q=Pthon');await started
  const input=page.getByRole('combobox',{name:'本を検索'})
  await input.fill('こころ');await input.press('Enter')
  await expect(page.getByText('2件ヒット')).toBeVisible()
  const finished=page.waitForResponse(r=>new URL(r.url()).searchParams.get('q')==='Python')
  release();await finished
  await expect(input).toHaveValue('こころ')
  await expect(page.getByText('次の検索結果を表示しています：',{exact:false})).toHaveCount(0)
})

test('failed correction is a retryable error, not an empty result',async({page})=>{
  await page.route('**/search-index.json',route=>route.fulfill({json:{...index,topics:[{id:'python',label:'Python',aliases:[],count:1}]}}))
  await page.route('**/api/books/search?**',route=>new URL(route.request().url()).searchParams.get('q')==='Python'?route.fulfill({status:503,json:{error:'temporary'}}):route.fulfill({json:{books:[],total:0}}))
  await page.goto('/search?q=Pthon')
  await expect(page.getByText('検索できませんでした。')).toBeVisible()
  await expect(page.getByText('見つかりませんでした',{exact:true})).toHaveCount(0)
})
