'use strict';
const assert=require('assert');
const yahoo=require('../extensions/lineupbeat-espn/yahoo-roster-parser.js');
const cbs=require('../extensions/lineupbeat-espn/cbs-roster-parser.js');

const yahooRoster=yahoo.parseEntries([
  {slot:'QB',name:'Josh Allen',playerId:'30977',meta:'Buf - QB'},
  {slot:'BN',name:'James Cook III',href:'https://sports.yahoo.com/nfl/players/34019',meta:'Buf - RB',status:'Q'},
  {slot:'DEF',name:'Buffalo',playerId:'10002',meta:'Buf - DEF'}
]);
assert.deepStrictEqual(yahooRoster.map(row=>[row.lineupSlot,row.name,row.team,row.position]),[
  ['QB','Josh Allen','BUF','QB'],['BE','James Cook III','BUF','RB'],['D/ST','Buffalo','BUF','D/ST']
]);
assert.throws(()=>yahoo.parseEntries([
  {slot:'QB',name:'Josh Allen',playerId:'30977',meta:'Buf - QB'},
  {slot:'QB',name:'Josh Allen',playerId:'30977',meta:'Buf - QB'}
]),new RegExp(yahoo.AMBIGUOUS_ERROR.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));

const cbsRoster=cbs.parseEntries([
  {slot:'QB',name:'Allen, Josh',href:'/players/playerpage/2181054/josh-allen/',meta:'QB BUF'},
  {slot:'Bench',name:'Cook, James III Q',href:'/fantasy/football/players/2963401/',meta:'BUF - RB',status:'Q'},
  {slot:'DST',name:'Buffalo Bills',href:'/players/407/',meta:'DEF BUF'}
]);
assert.deepStrictEqual(cbsRoster.map(row=>[row.lineupSlot,row.name,row.team,row.position]),[
  ['QB','Josh Allen','BUF','QB'],['BE','James Cook III','BUF','RB'],['D/ST','Buffalo Bills','BUF','D/ST']
]);
assert.throws(()=>cbs.parseEntries([
  {slot:'RB',name:'Bijan Robinson',href:'/players/2669445/',meta:'ATL RB'},
  {slot:'RB',name:'Bijan Robinson',href:'/players/2669445/',meta:'ATL RB'}
]),new RegExp(cbs.AMBIGUOUS_ERROR.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));

console.log('Yahoo and CBS roster parser tests passed.');
