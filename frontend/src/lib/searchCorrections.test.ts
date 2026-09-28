import { describe, it, expect } from 'vitest'
import { searchCorrections, typoDistance } from './searchCorrections'
import { buildSearchSuggestions } from './searchSuggestions'
import { isGenre, isTheme, type DiscoveryIndex } from './discoveryIndex'
const index: DiscoveryIndex = { version: 1, books: [
  {id:1,title:'Python入門',authors:'山田',title_reading:'パイソンニュウモン',authors_reading:'ヤマダ'},
  {id:2,title:'C言語によるPICプログラミング大全',authors:'著者'},
], topics:[{id:'python',kind:'theme',label:'Python',aliases:['パイソン'],count:1,genres:['genre-it']},
{id:'genre-it',kind:'genre',label:'IT・プログラミング',aliases:['IT'],count:1},
{id:'ndc-0',kind:'legacy',label:'総記・情報',aliases:[],count:1}],coverage:{books:2,page_count:0,level:0}}
describe('search recovery and reader genres',()=>{
 it('corrects deletion and transposition using real vocabulary',()=>{
  expect(searchCorrections(index,'Pythn')).toEqual(['Python'])
  expect(searchCorrections(index,'Pyhton')).toEqual(['Python'])
  expect(typoDistance('python','pyhton')).toBe(1)
  expect(searchCorrections(index,'C言語によるPICプログラミング大前')).toEqual(['C言語によるPICプログラミング大全'])
 })
 it('does not rewrite short terms, ISBNs or exact aliases',()=>{
  for(const q of ['C','C++','C#','AI','9780306406157','Python','パイソン']) expect(searchCorrections(index,q)).toEqual([])
  expect(searchCorrections(index,'まったく無関係な本')).toEqual([])
 })
 it('uses verified title/author readings in suggestions',()=>{
  expect(buildSearchSuggestions(index,'ぱいそんにゅう')[0].value).toBe('Python入門')
  expect(buildSearchSuggestions(index,'やまだ')[0].value).toBe('山田')
  expect(buildSearchSuggestions(index,'総記')).toEqual([])
 })
 it('separates user-facing genres/themes from legacy classifications',()=>{
  expect(index.topics.filter(isGenre).map(t=>t.id)).toEqual(['genre-it'])
  expect(index.topics.filter(isTheme).map(t=>t.id)).toEqual(['python'])
 })
})
