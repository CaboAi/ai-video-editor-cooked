// ask.js alert rules, run in node (no browser): a question chimes once per id, a video once when Claude finishes.
import fs from 'fs';
import assert from 'assert';
const m={};new Function('module',fs.readFileSync(new URL('../plugins/ai-editor/lib/ai_editor/ask.js',import.meta.url),'utf8'))(m);
const {firstTime,finished}=m.exports;
// the same question after a reload or in a second tab: no second chime
let seen=firstTime([],'q1');assert.deepEqual(seen,['q1']);
assert.equal(firstTime(seen,'q1'),null);
assert.deepEqual(firstTime(seen,'q2'),['q2','q1']);
assert.equal(firstTime(Array.from({length:40},(_,i)=>'x'+i),'q').length,30,'the list stays short');
// a draft finishes: cutting -> cut chimes, a new or unchanged video does not
let r=finished({a:'cutting',b:'cut'},{a:'cut',b:'cut',c:'cutting'});
assert.deepEqual(r.done,['a']);assert.deepEqual(r.next,{a:'cut',b:'cut',c:'cutting'});
// a question mid-make is not a finished draft, and answering it is not either; the render finishing later is
r=finished({a:'notes'},{a:'ask'});assert.deepEqual(r.done,[]);assert.equal(r.next.a,'notes');
r=finished(r.next,{a:'notes'});assert.deepEqual(r.done,[]);
r=finished(r.next,{a:'done'});assert.deepEqual(r.done,['a']);
assert.deepEqual(finished({},{a:'ask'}).next,{a:'ask'});
console.log('alerts ok');
