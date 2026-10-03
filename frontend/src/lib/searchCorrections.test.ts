import { describe, it, expect } from 'vitest'
import { searchCorrectionCandidates, searchCorrections, typoDistance } from './searchCorrections'
import { buildSearchSuggestions } from './searchSuggestions'
import { isGenre, isTheme, type DiscoveryIndex } from './discoveryIndex'
import { readFileSync } from 'node:fs'
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
 it('offers tied candidates without choosing alphabetically',()=>{
  const ambiguous:DiscoveryIndex={...index,books:[
   {id:1,title:'デザイン',authors:''}, {id:2,title:'デザイス',authors:''},
  ],topics:[]}
  const candidates=searchCorrectionCandidates(ambiguous,'デザイソ')
  expect(candidates.map(c=>c.query)).toEqual(expect.arrayContaining(['デザイン','デザイス']))
  expect(candidates.every(c=>!c.automatic)).toBe(true)
 })
 it('corrects only one token and keeps the other terms grounded in a book',()=>{
  const compound:DiscoveryIndex={...index,books:[{id:1,title:'Python データ分析',authors:'山田'}]}
  expect(searchCorrectionCandidates(compound,'Pythn データ分析')).toEqual([{query:'Python データ分析',distance:1,automatic:true}])
  expect(searchCorrectionCandidates(compound,'Pythn 山田')).toEqual([{query:'Python 山田',distance:1,automatic:true}])
  expect(searchCorrections(compound,'Pythn 料理')).toEqual([])
  expect(searchCorrections(compound,'Python 山田')).toEqual([])
 })
 it('corrects Japanese mixed input, missing kana and shape-confusable kana to the searchable alias',()=>{
  const japanese:DiscoveryIndex={...index,books:[{id:1,title:'デザイン入門',authors:'著者'}],topics:[
   {id:'genre-design',kind:'genre',label:'デザイン・アート',aliases:['デザイン','アート'],count:1},
  ]}
  for(const query of ['デザiン','デザインン','デザイソ','でざいソ']) {
   expect(searchCorrectionCandidates(japanese,query)).toEqual([{query:'デザイン',distance:1,automatic:true}])
  }
  // Three-character kana can offer a candidate, but must not auto-rewrite.
  expect(searchCorrectionCandidates(japanese,'デザン')).toEqual([{query:'デザイン',distance:1,automatic:false}])
  expect(searchCorrections(japanese,'デザイン')).toEqual([])
 })
 it('keeps meaning-changing kanji substitutions selectable rather than automatic',()=>{
  const subjects:DiscoveryIndex={...index,books:[{id:1,title:'化学入門',authors:''}],topics:[]}
  expect(searchCorrectionCandidates(subjects,'科学入門')).toEqual([{query:'化学入門',distance:1,automatic:false}])
 })
 it('does not rewrite existing authors or symbolic identifiers inside a multi-term query',()=>{
  const names:DiscoveryIndex={...index,books:[{id:1,title:'Python パイソン',authors:'Pythn'}]}
  expect(searchCorrections(names,'Pythn パイソン')).toEqual([])
  const unrelatedAuthor:DiscoveryIndex={...index,books:[{id:1,title:'Python 料理',authors:'著者'},{id:2,title:'別の本',authors:'Pythn'}]}
  expect(searchCorrections(unrelatedAuthor,'Pythn 料理')).toEqual([])
  expect(searchCorrections(index,'Pythn C++')).toEqual([])
  expect(searchCorrections(index,'ISBN: 9780306406157')).toEqual([])
 })
 it('recovers the requested Japanese examples against the actual catalog',()=>{
  const catalog=JSON.parse(readFileSync(new URL('../../public/search-index.json',import.meta.url),'utf8')) as DiscoveryIndex
  for(const query of ['デザiン','デザイソ','デザインン']) {
   expect(searchCorrectionCandidates(catalog,query)).toContainEqual({query:'デザイン',distance:1,automatic:true})
  }
  expect(searchCorrectionCandidates(catalog,'デザン')).toContainEqual({query:'デザイン',distance:1,automatic:false})
  expect(searchCorrectionCandidates(catalog,'UI デザiン')).toContainEqual({query:'UI デザイン',distance:1,automatic:true})
 })
})
