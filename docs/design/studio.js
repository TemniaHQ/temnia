(() => {
  'use strict';

  const paths = {
    chevron:'<path d="m8 10 4 4 4-4"/>',edit:'<path d="m15 4 5 5M4 20l5-1L20 8l-5-5L4 14z"/>',review:'<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m8 12 3 3 5-6"/>',feedback:'<path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3H3V6a2 2 0 0 1 2-2z"/><path d="M8 9h8m-8 4h5"/>',monitor:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M9 21h6m-3-4v4"/>',canvas:'<rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="15" y="15" width="6" height="6" rx="1.5"/><path d="M15 3h6v6M3 15v6h6M9 6h3v12h3"/>',command:'<path d="M9 9H6a3 3 0 1 1 3-3v12a3 3 0 1 1-3-3h12a3 3 0 1 1-3 3V6a3 3 0 1 1 3 3H9z"/>','arrow-up-right':'<path d="M6 18 18 6M6 6h12v12"/>',arrow:'<path d="M4 12h16m-5-5 5 5-5 5"/>',back:'<path d="M20 12H4m5-5-5 5 5 5"/>',expand:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',link:'<path d="m10 7 2-2a5 5 0 0 1 7 7l-2 2m-3 3-2 2a5 5 0 0 1-7-7l2-2m1 7 8-8"/>',external:'<path d="M13 4h7v7m-9 2 9-9M8 4H4v16h16v-4"/>',play:'<path d="m9 5 10 7-10 7z"/>',pause:'<path d="M8 5v14m8-14v14"/>','skip-back':'<path d="M5 5v14m14-14L8 12l11 7z"/>','skip-forward':'<path d="M19 5v14M5 5l11 7-11 7z"/>',spark:'<path d="m12 3 2 7 7 2-7 2-2 7-2-7-7-2 7-2z"/>',join:'<path d="M4 5v14m16-14v14M8 5h3v14H8m8-14h-3v14h3"/>',sources:'<rect x="3" y="6" width="18" height="15" rx="2"/><path d="M7 3h10m-7 7 6 4-6 4z"/>',chapters:'<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="16" rx="1.5"/><path d="m18 4 3 2v12l-3 2z"/>',transcript:'<path d="M5 4h14v16H5zM8 8h8m-8 4h8m-8 4h5"/>',captions:'<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 9H6v6h4m8-6h-4v6h4"/>',audio:'<path d="M4 9v6m4-10v14m4-16v18m4-13v8m4-5v2"/>',brand:'<path d="m12 3 8 5-3 12H7L4 8zM4 8h16M8 8l4 12 4-12m-4-5L8 8m4-5 4 5"/>',deliver:'<path d="M4 10v10h16V10M12 15V3m-4 4 4-4 4 4"/>',check:'<path d="m5 12 4 4L19 6"/>',close:'<path d="m6 6 12 12M18 6 6 18"/>',lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',crop:'<path d="M6 3v15h15M3 6h15v15"/>',volume:'<path d="M3 9h4l5-5v16l-5-5H3zM16 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',dots:'<circle cx="5" cy="12" r=".7"/><circle cx="12" cy="12" r=".7"/><circle cx="19" cy="12" r=".7"/>',grip:'<path d="M8 6h.1M8 12h.1M8 18h.1M16 6h.1M16 12h.1M16 18h.1" stroke-width="3"/>',folder:'<path d="M3 6h7l2 3h9v11H3z"/>',history:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7m2-5v6l4 2"/>'
  };
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name]||paths.spark}</svg>`;
  const $ = s => document.querySelector(s);
  const esc = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time = (seconds, decimals=false) => {
    const ticks = Math.round(seconds * (decimals?10:1));
    const minutes = Math.floor(ticks / (decimals?600:60));
    const remainder = (ticks % (decimals?600:60)) / (decimals?10:1);
    return String(minutes).padStart(2,'0') + ':' + remainder.toFixed(decimals?1:0).padStart(decimals?4:2,'0');
  };
  const hydrate = root => (root||document).querySelectorAll('[data-icon]').forEach(el=>el.outerHTML=icon(el.dataset.icon));
  const image = 'assets/studio-source.png';
  const chapters = [
    {id:0,n:'01',title:'The opening',start:0,end:72,description:'A question about the spaces we inhabit.'},
    {id:1,n:'02',title:'Challenging the brief',start:72,end:528,description:'What the client asks for, and what they really need.'},
    {id:2,n:'03',title:'Life inside buildings',start:528,end:1152,description:'Designing around the everyday rituals of living.'},
    {id:3,n:'—',title:'Off-topic exchange',start:1152,end:1167,drop:true},
    {id:4,n:'04',title:'Room for people',start:1167,end:1911,description:'The best spaces make room for the people who use them.'},
    {id:5,n:'05',title:'Material decisions',start:1911,end:2583,description:'Texture, light, and the choices that change a room.'},
    {id:6,n:'06',title:'What comes next',start:2583,end:3134,description:'A more human way to think about the built environment.'}
  ];
  const state = {mode:'edit',view:'stage',chapter:4,position:1572,tool:'transcript',popover:null,version:3,internal:false,client:false,approvedVersion:null,reviewedChapter:null,approvedChapter:null,captionStyle:'editorial',captions:true,captionSize:100,captionCopy:'Good spaces leave room|for people.',aspect:'16:9',safe:false,offset:0,appliedOffset:0,resolved:[false,false],selectedNote:0,playing:false,focus:false};
  const notes = [
    {name:'Mara',initials:'MR',time:'26:12',version:3,scope:'Client-visible',message:'Could “people” stay on screen a little longer? That’s the idea I want the viewer to leave with.',type:'captions'},
    {name:'Ari',initials:'AK',time:'31:51',version:3,scope:'Internal only',message:'Check the breath before we move into materials. Give both chapters a natural edge.',type:'boundary'}
  ];
  const tools = [['sources','Sources','#91b7ff'],['chapters','Chapters','#c4a3ff'],['transcript','Transcript','#e7be91'],['captions','Captions','#edafd6'],['audio','Sound','#91d9c9'],['brand','Brand','#efb198'],['deliver','Deliver','#b8c1eb']];
  const internalReady=()=>state.internal&&state.reviewedChapter===state.chapter;
  const clientReady=()=>state.client&&state.approvedChapter===state.chapter&&state.approvedVersion===state.version;
  let noticeTimer, playTimer, previousFocus, captionSnapshot = null, graphDrag = null;
  const graph = {scale:1,fitScale:1,x:0,y:0,nodes:[
    {id:'source',x:22,y:112,w:176,h:183},
    {id:'cover',x:267,y:75,w:252,h:211},
    {id:'moment',x:606,y:13,w:196,h:177},
    {id:'quote',x:610,y:220,w:192,h:127},
    {id:'review',x:890,y:112,w:198,h:188}
  ],edges:[['source','cover'],['cover','moment'],['cover','quote'],['moment','review'],['quote','review']]};

  function notify(message){clearTimeout(noticeTimer);$('#notification').textContent=message;$('#notification').hidden=false;noticeTimer=setTimeout(()=>$('#notification').hidden=true,4400);}
  function draftChange(){state.version++;state.internal=false;state.client=false;$('#save-state').textContent=`Draft v${state.version} saved in this session`;syncVersion();}
  function syncVersion(){$('#monitor-version').textContent='v'+state.version;}
  function panelHeader(title,name,badge=''){return `<div class="panel-header"><div class="row">${icon(name)}${title}</div>${badge||`<button class="tiny-button" data-action="context-menu" aria-label="More ${title.toLowerCase()} actions">${icon('dots')}</button>`}</div>`;}
  function renderDock(){
    $('#tool-dock').innerHTML=tools.map(([id,label,color])=>`<button class="tool" data-tool="${id}" style="--tool-color:${color}" aria-label="${label}" aria-pressed="${state.tool===id}" title="${label}"><span class="tool-bridge"></span><span class="tool-illustration">${icon(id)}</span><span class="tool-text">${label}</span></button>`).join('');
  }
  function renderCompass(){
    const c=chapters[state.chapter];
    const heading=state.mode==='edit'?'CHAPTER IN FOCUS':state.mode==='review'?'YOUR REVIEW':'PINNED TO THE FRAME';
    const big=state.mode==='edit'?c.n:state.mode==='review'?'v'+state.version:String(state.resolved.filter(x=>!x).length).padStart(2,'0');
    const title=state.mode==='edit'?c.title:state.mode==='review'?'The version they will see.':'A place for every note.';
    const description=state.mode==='edit'?c.description:state.mode==='review'?'Watch it in context. Inspect the evidence. Make the call.':'Move from the conversation directly to the edit.';
    $('#chapter-compass').innerHTML=`<div class="eyebrow">${heading}</div><div class="chapter-number">${big}</div><h1>${title}</h1><p>${description}</p><div class="chapter-range">${icon('clock')}<span>${time(c.start)} — ${time(c.end)}</span></div><div class="chapter-mini-nav"><button data-action="previous-chapter" aria-label="Previous chapter">${icon('back')}</button><button data-action="next-chapter" aria-label="Next chapter">${icon('arrow')}</button></div>`;
  }
  function transcriptPanel(){
    const c=chapters[state.chapter];
    const chapterText={0:'What makes a place feel like it belongs to you?',1:'The brief tells you what to build. Listening tells you why.',2:'People have rituals. A good building gives those rituals a home.',4:'Good spaces leave room for people.',5:'Materials change how you feel before you can explain why.',6:'We need to ask better questions about the places we make.'}[state.chapter]||'A short exchange, restored to the source plan.';
    const isMain=state.chapter===4;
    return panelHeader('Transcript','transcript','<span class="pill">Following playhead</span>')+`<div class="panel-body"><div class="speaker-line"><span class="speaker-badge">MS</span>MAYA SATO <span class="mono" style="margin-left:auto">${time(state.position)}</span></div><div class="transcript-words">${isMain?`<span class="past">We spend so much time asking what a building should look like.</span> <button data-action="seek-phrase">But I think the better question is, <mark>how do we want people to feel inside it?</mark></button>`:`<span class="past">${esc(c.description||'A short exchange from the original recording.')}</span> <button data-action="seek-phrase"><mark>${chapterText}</mark></button>`}</div><span class="transcript-time">${isMain?'26:18':time(Math.min(c.end-1,state.position+6))}</span><p class="transcript-after">${isMain?'Because good spaces leave room for people. For their habits, their mess, and the things you didn’t plan for.':'Listen to the complete thought before deciding where it ends.'}</p></div><div class="panel-bottom"><span>Click a phrase to seek</span><button data-action="caption-from-text">Use as caption ${icon('arrow-up-right')}</button></div>`;
  }
  function reviewPanel(){
    return panelHeader('Ready for a human','review',`<span class="pill ${internalReady()?'good':'warn'}">${internalReady()?'Reviewed':'Your turn'}</span>`)+`<div class="panel-body"><div class="panel-smalltitle">${chapters[state.chapter].title} <span class="muted">/ v${state.version}</span></div>${[['The thought is complete','Setup, idea, and payoff stay together.'],['The source is attached','Every retained range leads back to the master.'],['Picture, sound, captions','Illustrative checks: no open flags.']].map(([a,b])=>`<div class="check-item">${icon('check')}<div><span>${a}</span><p>${b}</p></div></div>`).join('')}<div class="check-stage"><span class="${internalReady()?'mint':'coral'}">Internal ${internalReady()?'✓':''}</span><span class="stage-line"></span><span class="${clientReady()?'mint':''}">Client ${clientReady()?'✓':''}</span><span class="stage-line"></span><span>Delivery</span></div><button class="btn primary panel-action" data-action="approve" ${internalReady()?'disabled':''}>${icon('check')}${internalReady()?`v${state.version} approved internally`:`Approve version ${state.version}`}</button><p class="review-footnote">${internalReady()?'This exact version is ready for client review.':'The checks inform your decision. Approval stays yours.'}</p></div><div class="panel-bottom"><button data-action="evidence">${icon('link')} Original context</button><button data-action="handoff">${internalReady()?'Client review':'Approval path'} ${icon('arrow')}</button></div>`;
  }
  function feedbackPanel(){
    return panelHeader('The conversation','feedback',`<span class="pill">${state.resolved.filter(x=>!x).length} open</span>`)+`<div class="panel-body">${notes.map((n,i)=>`<button class="feedback-card ${state.selectedNote===i?'active':''} ${state.resolved[i]?'resolved':''}" data-note="${i}" aria-pressed="${state.selectedNote===i}"><div class="feedback-person"><span class="avatar">${n.initials}</span><span>${n.name}</span><span class="mono">${n.time}</span></div><p>${esc(n.message)}</p><small>${n.scope} · v${n.version} ${state.resolved[i]?'· Resolved':''}</small></button>`).join('')}<p class="feedback-response">Notes keep their original frame and version.</p></div><div class="panel-bottom"><button data-action="edit-note">${icon('edit')} Edit this ${notes[state.selectedNote].type==='captions'?'caption':'boundary'}</button><button data-action="resolve">${state.resolved[state.selectedNote]?'Reopen':'Resolve'} ${icon('check')}</button></div>`;
  }
  function renderPanel(){
    $('#floating-panel').innerHTML=state.mode==='edit'?transcriptPanel():state.mode==='review'?reviewPanel():feedbackPanel();
    $('#frame-note').hidden=state.mode!=='feedback';
    $('#frame-note').textContent=String(state.selectedNote+1);
  }
  function renderReel(){
    $('#chapter-reel').innerHTML=chapters.map(c=>c.drop?`<button class="reel-drop" data-action="restore" aria-label="Review the 15-second deliberate drop"><span>15s</span></button>`:`<button class="reel-chapter ${state.chapter===c.id?'active':''} ${c.id===3?'is-restored':''}" data-chapter="${c.id}" aria-label="Chapter ${c.n}, ${esc(c.title)}, ${time(c.start)} to ${time(c.end)}" aria-pressed="${state.chapter===c.id}"><div class="reel-image"><img src="${image}" alt="" style="object-position:${[20,40,60,45,28,70,85][c.id]}% center;filter:${c.id===1?'saturate(.7)':c.id===5?'brightness(.8)':'none'}"><span class="reel-number">${c.n}</span></div><div class="reel-info"><b>${c.title}</b><span>${time(c.start)} — ${time(c.end)}</span></div></button>`).join('');
    const kept=chapters.filter(c=>!c.drop).length;
    $('#coverage-state').innerHTML=`${kept} chapters <span class="dim">·</span> ${chapters[3].drop?'1 deliberate drop':'Full source kept'}`;
    $('#drop-label').textContent=chapters[3].drop?'15s excluded':'52:14 accounted for';
  }
  function updateTime(){
    $('#play-time').textContent=time(state.position);
    $('#scrub').value=state.position;
    $('#scrub').style.background=`linear-gradient(to right,#baa1dc ${state.position/3134*100}%,#5d5065 ${state.position/3134*100}%)`;
  }
  function renderCaption(){
    const caption=$('#caption');
    caption.hidden=!state.captions;
    caption.className='caption '+(state.captionStyle==='block'?'block':state.captionStyle==='minimal'?'minimal':'neon');
    const parts=state.captionCopy.split('|');
    caption.innerHTML=parts.map((p,i)=>`<span>${esc(p).replace(/people\./,'<em>people.</em>')}</span>${i<parts.length-1?'<br>':''}`).join('');
    caption.style.scale=String(state.captionSize/100);
  }
  function selectChapter(id){
    if(chapters[id].drop){actions.restore();return;}
    state.chapter=id;state.position=chapters[id].start+Math.min(20,(chapters[id].end-chapters[id].start)/2);
    $('#monitor-title').textContent=`${chapters[id].n} · ${chapters[id].title}`;
    $('#picture-range').textContent=`${time(chapters[id].start)} — ${time(chapters[id].end)}`;
    renderCompass();renderPanel();renderReel();updateTime();
  }
  function setMode(mode){
    state.mode=mode;closePopover();$('#studio').dataset.mode=mode;
    document.querySelectorAll('[data-mode]').forEach(el=>{if(el.tagName==='BUTTON')el.setAttribute('aria-pressed',el.dataset.mode===mode);});
    renderCompass();renderPanel();
    $('#room-bottom-hint').innerHTML=icon(mode==='review'?'review':mode==='feedback'?'feedback':'spark')+`<span>${mode==='edit'?'Your edit, with the whole story behind it.':mode==='review'?'Evidence close. Decisions clear.':"A note is the beginning of an edit."}</span>`;
    if(state.view==='canvas')setView('stage');
  }
  function setView(view){
    state.view=view;$('#studio').dataset.view=view;$('#stage-world').hidden=view==='canvas';$('#canvas-surface').hidden=view!=='canvas';
    document.querySelectorAll('button[data-view]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.view===view));
    closePopover();
    if(view==='canvas'){state.tool='chapters';renderGraph();fitGraph();}else if(state.tool==='chapters')state.tool='transcript';
    renderDock();
  }
  function stopPlay(){clearInterval(playTimer);state.playing=false;document.querySelector('[data-action="play"]').innerHTML=icon('play');document.querySelector('[data-action="play"]').setAttribute('aria-label','Play sample timeline');}
  function togglePlay(){
    if(state.playing){stopPlay();return;}
    state.playing=true;document.querySelector('[data-action="play"]').innerHTML=icon('pause');document.querySelector('[data-action="play"]').setAttribute('aria-label','Pause sample timeline');
    notify('Sample timeline playback · the generated frame is still, with no audio track.');
    playTimer=setInterval(()=>{state.position=Math.min(3134,state.position+.25);updateTime();if(state.position>=3134)stopPlay();},250);
  }

  function openDialog(title,content){
    stopPlay();closePopover();previousFocus=document.activeElement;
    $('#dialog-backdrop').innerHTML=`<section class="dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="dialog-head"><h2>${title}</h2><button class="tiny-button" data-action="close-dialog" aria-label="Close dialog">${icon('close')}</button></div>${content}</section>`;
    $('#dialog-backdrop').hidden=false;
    $('#dialog-backdrop').querySelector('button,input,textarea')?.focus();
  }
  function closeDialog(){$('#dialog-backdrop').hidden=true;$('#dialog-backdrop').innerHTML='';if(previousFocus?.isConnected)previousFocus.focus();}
  function closePopover(commit=false){if(captionSnapshot&&!commit){Object.assign(state,captionSnapshot);renderCaption();}captionSnapshot=null;state.popover=null;$('#tool-popover').hidden=true;}
  function popoverHeader(label,name){return `<div class="popover-header"><div class="row">${icon(name)}<h2>${label}</h2></div><button class="tiny-button" data-action="close-popover" aria-label="Close tool">${icon('close')}</button></div>`;}
  function openTool(tool){
    if(state.popover===tool){closePopover();return;}
    closePopover();
    state.tool=tool;renderDock();
    if(tool==='deliver'){actions.handoff();return;}
    if(tool==='chapters'){setView(state.view==='canvas'?'stage':'canvas');return;}
    if(tool==='transcript'){setMode('edit');state.tool='transcript';renderDock();return;}
    state.popover=tool;
    if(tool==='captions')captionSnapshot={captionStyle:state.captionStyle,captions:state.captions,captionSize:state.captionSize,captionCopy:state.captionCopy};
    const content={
      sources:()=>popoverHeader('Source library','sources')+`<div class="file-card"><img src="${image}" alt="Generated source thumbnail"><div><b>Rethinking space · Master</b><p>52:14 · 4K master · English</p></div></div><div class="popover-row"><span>Maya / Camera A</span><span class="pill good">Aligned</span></div><div class="popover-row"><span>Ari / Camera B</span><span class="pill good">Aligned</span></div><button class="btn panel-action" data-action="source-details">Inspect source ${icon('arrow')}</button><p class="popover-footnote">Sample source group. Production intake begins with your original files.</p>`,
      captions:()=>popoverHeader('Make the words visible','captions')+`<span class="popover-label">Caption style</span><div class="preset-row">${[['minimal','Quiet'],['block','Bold'],['editorial','Editorial']].map(([id,label])=>`<button class="preset ${state.captionStyle===id?'active':''}" data-preset="${id}" aria-pressed="${state.captionStyle===id}">${label}</button>`).join('')}</div><label class="popover-label" for="caption-copy">Caption · use | for a line break</label><input class="text-input" id="caption-copy" value="${esc(state.captionCopy)}"><div class="range-row"><label for="caption-size">Size</label><input id="caption-size" type="range" min="75" max="135" value="${state.captionSize}" step="5"><span class="mono" id="caption-size-value">${state.captionSize}%</span></div><div class="popover-row" style="margin-top:13px"><label for="caption-on">Show captions</label><input class="toggle" id="caption-on" type="checkbox" ${state.captions?'checked':''}></div><button class="btn primary panel-action" data-action="save-captions">Save caption changes</button>`,
      audio:()=>popoverHeader('Listen to the room','audio')+`<div class="popover-row"><span>Maya / Camera A</span><span class="pill">Speech track</span></div><div class="range-row"><label for="audio-level">Level</label><input id="audio-level" type="range" min="-12" max="6" value="0"><span class="mono" id="audio-value">0 dB</span></div><div class="popover-row" style="margin-top:16px"><label for="clean-audio">Reduce room noise</label><input class="toggle" type="checkbox" id="clean-audio"></div><div class="popover-row"><label for="enhance-voice">Enhance voice</label><input class="toggle" type="checkbox" id="enhance-voice"></div><p class="popover-footnote">Placement concept. An audio track and a before/after preview are needed to judge these changes; this sample has no sound.</p>`,
      brand:()=>popoverHeader('Form & Feel','brand')+`<span class="popover-label">Approved brand kit · version 02</span><div class="popover-row"><span>Palette</span><div class="swatches"><span class="swatch" style="background:#1a1830"></span><span class="swatch" style="background:#b4a0d5"></span><span class="swatch" style="background:#efb998"></span><span class="swatch" style="background:#eee6e2"></span></div></div><div class="popover-row"><span>Voice</span><span>Thoughtful · Human · Precise</span></div><div class="popover-row"><span>Captions</span><span>Editorial</span></div><button class="btn primary panel-action" data-action="apply-brand">Apply caption style</button><p class="popover-footnote">Brand choices belong to Form & Feel. Other clients keep their own kits and voice.</p>`
    };
    $('#tool-popover').innerHTML=content[tool]();$('#tool-popover').hidden=false;
  }

  function nodeContent(id){
    if(id==='source')return `<img class="node-photo" src="${image}" alt="Generated source thumbnail"><h3>Rethinking space</h3><p>52:14 · Master + 2 tracks</p><div class="node-status">${icon('check')} Source ready</div>`;
    if(id==='cover')return `<h3>The whole episode</h3><p>${chapters.filter(c=>!c.drop).length} chapter ranges. ${chapters[3].drop?"One deliberate drop.":"Full source kept."}</p><div class="node-cover">${chapters.filter(c=>!c.drop).map(c=>`<span class="${c.id===4?'selected':''}">${c.n}</span>`).join('')}</div><div class="node-status">${icon('link')} 52:14 accounted for</div><button class="btn primary" data-action="open-chapter">Open chapter 04 ${icon('arrow')}</button>`;
    if(id==='moment')return `<img class="node-photo" src="${image}" alt="Generated moment thumbnail" style="height:67px;object-position:30% center"><h3>Make room for people</h3><p>26:04–26:42 · 3 aspect variants</p><div class="node-status">${icon('edit')} Draft v${state.version}</div>`;
    if(id==='quote')return `<div class="node-quote">Good spaces leave<br>room for <em>people.</em></div><div class="node-status">${icon('link')} 26:18–26:22 · Source quote</div>`;
    return `<h3>A version they can approve</h3><p>Internal review, then client sign-off. The approval follows this exact edit.</p><div class="node-status">${icon(internalReady()?'check':'clock')} ${internalReady()?'Internally approved':'Internal review pending'}</div><button class="btn" data-action="go-review">Open review ${icon('arrow')}</button>`;
  }
  function renderGraph(){
    const labels={source:['ORIGINAL MASTER','sources'],cover:['COVERAGE / CHAPTERS','chapters'],moment:['MOMENT / 00:38','monitor'],quote:['QUOTE CARD','brand'],review:['REVIEW','review']};
    $('#canvas-world').innerHTML=`<svg class="graph-wires" id="graph-wires" viewBox="0 0 1160 360" aria-hidden="true"></svg>`+graph.nodes.map(n=>`<article class="graph-node ${n.id==='cover'?'selected':''}" data-node="${n.id}" style="left:${n.x}px;top:${n.y}px;width:${n.w}px;min-height:${n.h}px"><button class="node-grip" data-drag="${n.id}" aria-label="Move ${labels[n.id][0].toLowerCase()} node with arrow keys"><span class="row">${icon(labels[n.id][1])}${labels[n.id][0]}</span>${icon('grip')}</button><div class="node-content">${nodeContent(n.id)}</div></article>`).join('');
    graph.nodes.forEach(n=>{n.h=document.querySelector(`[data-node="${n.id}"]`).offsetHeight;});
    drawWires();
  }
  function drawWires(){
    $('#graph-wires').innerHTML=graph.edges.map(([from,to])=>{const a=graph.nodes.find(n=>n.id===from),b=graph.nodes.find(n=>n.id===to);const x1=a.x+a.w,y1=a.y+a.h/2,x2=b.x,y2=b.y+b.h/2;return `<path d="M ${x1} ${y1} C ${x1+60} ${y1},${x2-60} ${y2},${x2} ${y2}" fill="none" stroke="#ba91d660" stroke-width="1.2"/><circle cx="${x1}" cy="${y1}" r="3" fill="#9982b1"/><circle cx="${x2}" cy="${y2}" r="3" fill="#9982b1"/>`;}).join('');
  }
  function graphTransform(){$('#canvas-world').style.transform=`translate(${graph.x}px,${graph.y}px) scale(${graph.scale})`;$('#zoom-value').textContent=Math.round(graph.scale/graph.fitScale*100)+'%';}
  function fitGraph(){const rect=$('#canvas-viewport').getBoundingClientRect();const minX=Math.min(...graph.nodes.map(n=>n.x))-20,minY=Math.min(...graph.nodes.map(n=>n.y))-10;const maxX=Math.max(...graph.nodes.map(n=>n.x+n.w))+20,maxY=Math.max(...graph.nodes.map(n=>n.y+n.h))+10;const w=maxX-minX,h=maxY-minY;graph.fitScale=Math.min(rect.width/w,rect.height/h,1);graph.scale=graph.fitScale;graph.x=(rect.width-w*graph.scale)/2-minX*graph.scale;graph.y=(rect.height-h*graph.scale)/2-minY*graph.scale;graphTransform();}
  function zoomBy(factor){const rect=$('#canvas-viewport').getBoundingClientRect();const next=Math.min(graph.fitScale*2.5,Math.max(graph.fitScale*.7,graph.scale*factor));const ratio=next/graph.scale;graph.x=rect.width/2-(rect.width/2-graph.x)*ratio;graph.y=rect.height/2-(rect.height/2-graph.y)*ratio;graph.scale=next;graphTransform();}

  function wave(seed){return `<svg viewBox="0 0 240 50" preserveAspectRatio="none" aria-hidden="true">${Array.from({length:60},(_,i)=>{const h=4+Math.abs(Math.sin(i*1.3+seed)*Math.cos(i*.46))*38;return `<rect x="${i*4}" y="${25-h/2}" width="1.6" height="${h}" rx=".8" fill="currentColor"/>`;}).join('')}</svg>`;}
  function boundaryDialog(){
    state.offset=state.appliedOffset;
    openDialog('One seam. Two chapters.',`<p>Move the shared cut within the silence. Both chapter edges follow the same point.</p><div class="boundary-lens"><div class="boundary-labels"><div><b>04 · Room for people</b><span>“…the people who will use it.”</span></div><div><b>05 · Material decisions</b><span>“Let’s talk about materials.”</span></div></div><div class="boundary-wave">${wave(2)}<div class="shared-pause"><span>1.2s pause</span><i class="cut-blade" id="cut-blade" style="left:${50+state.offset/20}%"></i></div>${wave(6)}</div><div class="boundary-controls"><div style="flex:1;min-width:150px"><label for="cut-range">Shared cut · 100ms steps</label><input type="range" id="cut-range" min="-500" max="500" value="${state.offset}" step="100" style="width:100%;margin-top:10px" aria-valuetext="${time(1911+state.offset/1000,true)}"></div><div class="boundary-values"><div><span>04 ends</span><div class="mono cut-time">${time(1911+state.offset/1000,true)}</div></div><div><span>05 begins</span><div class="mono cut-time">${time(1911+state.offset/1000,true)}</div></div></div></div></div><p class="sample-note">Illustrative waveform. A real edit rechecks both neighbours and identifies affected renders. A new version needs review again.</p><div class="dialog-actions"><button class="btn quiet" data-action="close-dialog">Cancel</button><button class="btn warm" data-action="commit-cut" ${state.offset===state.appliedOffset?'disabled':''}>Apply to both chapters ${icon('join')}</button></div>`);
  }
  function handoffDialog(){
    const ready=clientReady();
    openDialog('The version that leaves the room',`<div class="dialog-picture"><img src="${image}" alt="Generated review frame"><span class="pill">${chapters[state.chapter].title} · v${state.version}</span></div><p>Each approval belongs to an exact asset version. Delivery checks the version again.</p><div class="deliver-steps"><div class="deliver-step"><div class="eyebrow">01 / INTERNAL</div><h3 style="margin-top:8px">${internalReady()?'Approved':'Your decision'}</h3><p>${internalReady()?`Version ${state.version} passed internal review.`:'Inspect the evidence and approve this edit.'}</p><button class="btn" data-action="go-review">${internalReady()?'View review':'Open review'}</button></div><div class="deliver-step"><div class="eyebrow">02 / CLIENT</div><h3 style="margin-top:8px">${ready?'Approved':'Client sign-off'}</h3><p>${ready?`Version ${state.version} is approved by the client.`:'A simple branded surface for the client.'}</p><button class="btn" data-action="client-view" ${internalReady()?'':'disabled'}>Client preview</button></div><div class="deliver-step"><div class="eyebrow">03 / DELIVERY</div><h3 style="margin-top:8px">${ready?'Ready to prepare':'Version protected'}</h3><p>${ready?'The approved edition can be prepared.':'Current-version approval is required.'}</p><button class="btn primary" data-action="manifest" ${ready?'':'disabled'}>Prepare manifest</button></div></div><p class="sample-note">Local prototype only. Rights and format checks are sample states; no files are rendered or transmitted.</p>`);
  }
  const actions={
    'close-dialog':closeDialog,'close-popover':closePopover,
    projects(){openDialog('Your studio',`<div class="eyebrow" style="color:#a98cb9;margin-bottom:12px">FORM & FEEL / SEPTEMBER COLLECTION</div><button class="project-card" data-action="return-project"><img src="${image}" alt="Generated episode thumbnail"><div><h3>Rethinking space</h3><p>EP. 08 · 52:14 · Continue at chapter 04</p></div>${icon('arrow')}</button><div class="detail-row"><span>Workspace</span><span>Form & Feel</span></div><div class="detail-row"><span>Brand</span><span>Living Architecture · Kit 02</span></div><p class="sample-note">One fictional project is available in this concept. The production workspace switcher also scopes clients, brands, and permissions.</p>`);},
    'return-project'(){closeDialog();setMode('edit');setView('stage');selectChapter(4);},
    command(){openDialog('Jump to the work',`${[['Edit','edit','1'],['Review','review','2'],['Feedback','feedback','3']].map(([name,id,k])=>`<button class="command-item" data-mode="${id}">${icon(id)}${name}<kbd>⌥ ${k}</kbd></button>`).join('')}<button class="command-item" data-action="canvas">${icon('canvas')}Episode canvas</button><button class="command-item" data-action="boundary">${icon('join')}Refine a shared boundary</button><button class="command-item" data-action="projects">${icon('folder')}Switch project</button>`);},
    handoff:handoffDialog,
    'go-review'(){closeDialog();setMode('review');},
    'client-view'(){if(!internalReady())return;openDialog('Client review preview',`<div class="client-review"><div class="eyebrow">FORM & FEEL / FOR LIVING ARCHITECTURE</div><h3>${chapters[state.chapter].title}.</h3><img src="${image}" alt="Generated review frame"><p>Review copy · Version ${state.version} · Source range ${time(chapters[state.chapter].start)}–${time(chapters[state.chapter].end)}</p><div class="dialog-actions"><button class="btn" data-action="client-changes">Request changes</button><button class="btn primary" data-action="approve-client" ${clientReady()?'disabled':''}>${clientReady()?'Approved':`Approve version ${state.version}`}</button></div></div><p class="sample-note">Client-surface preview. Production clients see this review surface without the agency workspace.</p>`);},
    'approve-client'(){if(!internalReady())return;state.client=true;state.approvedVersion=state.version;state.approvedChapter=state.chapter;closeDialog();renderPanel();handoffDialog();notify(`Client approval pinned to v${state.version} in this local session.`);},
    'client-changes'(){openDialog('A note for the studio',`<label for="client-note">What should change?</label><textarea id="client-note" class="text-input" rows="4" placeholder="Leave a note about this version…"></textarea><div class="dialog-actions"><button class="btn primary" data-action="save-client-note">Add sample note</button></div>`);},
    'save-client-note'(){const text=$('#client-note').value.trim();if(!text){$('#client-note').focus();return;}notes[0].message=text;notes[0].version=state.version;state.resolved[0]=false;state.client=false;closeDialog();setMode('feedback');updateNoteCount();notify('Client note saved locally. This version is awaiting approval.');},
    manifest(){if(!clientReady())return;openDialog('Approved edition / manifest',`<p>This local example identifies the exact approved edit. No media is rendered or published.</p><pre class="manifest">${esc(JSON.stringify({prototype:true,project:'Rethinking space / EP. 08',asset:chapters[state.chapter].title,version:state.version,approvedVersion:state.approvedVersion,sourceRange:{in:time(chapters[state.chapter].start,true),out:time(chapters[state.chapter].end,true)},aspect:'16:9',destination:'Review package',transmitted:false},null,2))}</pre><div class="dialog-actions"><button class="btn primary" data-action="close-dialog">Done</button></div>`);},
    approve(){state.internal=true;state.reviewedChapter=state.chapter;renderPanel();renderCompass();notify(`Version ${state.version} approved internally. The client reviews this exact version next.`);},
    canvas(){closeDialog();setView('canvas');},
    'open-chapter'(){setView('stage');selectChapter(4);},
    'zoom-in'(){zoomBy(1.2);},'zoom-out'(){zoomBy(1/1.2);},fit:fitGraph,
    boundary(){closeDialog();boundaryDialog();},
    'commit-cut'(){state.appliedOffset=state.offset;chapters[4].end=1911+state.offset/1000;chapters[5].start=chapters[4].end;draftChange();closeDialog();renderReel();renderCompass();renderPanel();$('#picture-range').textContent=`${time(chapters[state.chapter].start)} — ${time(chapters[state.chapter].end)}`;notify(`Both chapter edges now meet at ${time(chapters[4].end,true)}. Draft v${state.version} needs review.`);},
    restore(){if(!chapters[3].drop){notify('Every second of the 52:14 original source is kept.');return;}openDialog('A deliberate pause in the plan',`<div class="row" style="margin-bottom:17px"><span class="pill warn">Proposed drop</span><span class="mono">19:12–19:27 · 15 seconds</span></div><p>A short off-topic exchange about adjusting the microphone. The original recording is preserved; this range is excluded from the chapter outputs.</p><div class="detail-row"><span>Original source coverage</span><span>51:59 kept + 00:15 drop</span></div><div class="dialog-actions"><button class="btn quiet" data-action="close-dialog">Keep the proposal</button><button class="btn primary" data-action="restore-drop">Restore this range</button></div>`);},
    'restore-drop'(){chapters[3].drop=false;chapters[3].n='03b';chapters[3].description='The excluded exchange, restored by the editor.';draftChange();closeDialog();renderReel();renderPanel();if(state.view==='canvas')renderGraph();notify('15 seconds restored. All 52:14 of the source is now kept.');},
    'previous-chapter'(){const ids=chapters.filter(c=>!c.drop).map(c=>c.id);selectChapter(ids[Math.max(0,ids.indexOf(state.chapter)-1)]);},
    'next-chapter'(){const ids=chapters.filter(c=>!c.drop).map(c=>c.id);selectChapter(ids[Math.min(ids.length-1,ids.indexOf(state.chapter)+1)]);},
    'seek-phrase'(){state.position=state.chapter===4?1572:chapters[state.chapter].start+5;updateTime();notify(`Source playhead moved to ${time(state.position)}.`);},
    play:togglePlay,back(){state.position=Math.max(0,state.position-5);updateTime();},forward(){state.position=Math.min(3134,state.position+5);updateTime();},
    focus(){state.focus=!state.focus;$('#studio').classList.toggle('focus',state.focus);document.querySelector('[data-action="focus"]').setAttribute('aria-pressed',state.focus);},
    evidence(){openDialog('Follow the thought back',`<div class="dialog-picture"><img src="${image}" alt="Generated original-source frame"><span class="pill">Original master · 26:04–26:42</span></div><p>“Because good spaces leave room for people. For their habits, their mess, and the things you didn’t plan for.”</p><div class="detail-row"><span>Chapter</span><span>04 · Room for people</span></div><div class="detail-row"><span>Quote anchor</span><span class="mono">26:18–26:22 · Transcript revision 1</span></div><div class="detail-row"><span>Provenance</span><span>Direct quotation · Sample data</span></div><div class="dialog-actions"><button class="btn primary" data-action="seek-evidence">Go to the source range</button></div>`);},
    'seek-evidence'(){closeDialog();setView('stage');selectChapter(4);state.position=1578;updateTime();notify('Sample source range selected at 26:18.');},
    'source-details'(){actions.evidence();},
    'context-menu'(){openDialog('Source and version',`<div class="detail-row"><span>Current edit</span><span>v${state.version}</span></div><div class="detail-row"><span>Transcript revision</span><span>1</span></div><div class="detail-row"><span>Client-approved version</span><span>${state.approvedVersion?'v'+state.approvedVersion:'None yet'}</span></div><div class="dialog-actions"><button class="btn" data-action="evidence">Original context</button><button class="btn primary" data-action="boundary">Shared boundary</button></div>`);},
    'caption-from-text'(){openTool('captions');},
    'save-captions'(){state.captionCopy=$('#caption-copy').value.trim()||'Good spaces leave room|for people.';draftChange();renderCaption();closePopover(true);renderPanel();notify(`Caption changes saved as draft v${state.version}.`);},
    'apply-brand'(){state.captionStyle='editorial';state.captionSize=100;draftChange();renderCaption();closePopover();renderPanel();notify('Form & Feel’s editorial caption style applied to a new draft.');},
    aspect(){state.tool='reframe';state.popover='reframe';renderDock();$('#tool-popover').hidden=false;$('#tool-popover').innerHTML=popoverHeader('A different frame','crop')+`<span class="popover-label">Aspect variant · inherits the base edit</span><div class="reframe-options">${[['16:9','Landscape',''],['9:16','Portrait','portrait'],['1:1','Square','square']].map(([id,label,cl])=>`<button class="reframe-option ${state.aspect===id?'active':''}" data-aspect="${id}" aria-pressed="${state.aspect===id}"><span class="ratio-box ${cl}"></span><span>${id} · ${label}</span></button>`).join('')}</div><div class="popover-row" style="margin-top:15px"><label for="safe-toggle">Show safe area</label><input class="toggle" id="safe-toggle" type="checkbox" ${state.safe?'checked':''}></div><p class="popover-footnote">Crop preview only. Source content and chapter timing remain unchanged.</p>`;},
    'select-note'(){state.selectedNote=0;renderPanel();},
    'edit-note'(){const type=notes[state.selectedNote].type;setMode('edit');if(type==='boundary')boundaryDialog();else openTool('captions');},
    resolve(){state.resolved[state.selectedNote]=!state.resolved[state.selectedNote];renderPanel();renderCompass();updateNoteCount();notify(state.resolved[state.selectedNote]?'Note resolved. Asset approval is a separate decision.':'Note reopened.');}
  };
  function updateNoteCount(){$('#feedback-count').textContent=state.resolved.filter(x=>!x).length;}

  document.addEventListener('click',event=>{
    const mode=event.target.closest('button[data-mode]');if(mode){closeDialog();setMode(mode.dataset.mode);return;}
    const view=event.target.closest('[data-view]');if(view?.tagName==='BUTTON'){setView(view.dataset.view);return;}
    const chapter=event.target.closest('[data-chapter]');if(chapter){selectChapter(Number(chapter.dataset.chapter));return;}
    const tool=event.target.closest('[data-tool]');if(tool){openTool(tool.dataset.tool);return;}
    const note=event.target.closest('[data-note]');if(note){state.selectedNote=Number(note.dataset.note);const n=notes[state.selectedNote];state.position=Number(n.time.split(':')[0])*60+Number(n.time.split(':')[1]);updateTime();renderPanel();return;}
    const preset=event.target.closest('[data-preset]');if(preset){state.captionStyle=preset.dataset.preset;document.querySelectorAll('[data-preset]').forEach(p=>{p.classList.toggle('active',p===preset);p.setAttribute('aria-pressed',p===preset);});renderCaption();return;}
    const aspect=event.target.closest('[data-aspect]');if(aspect){state.aspect=aspect.dataset.aspect;$('#aspect-label').textContent=state.aspect;const pic=$('#source-picture');pic.style.objectFit=state.aspect==='16:9'?'cover':'contain';pic.style.width=state.aspect==='9:16'?'40%':state.aspect==='1:1'?'66%':'100%';pic.style.margin='auto';document.querySelectorAll('[data-aspect]').forEach(a=>{a.classList.toggle('active',a===aspect);a.setAttribute('aria-pressed',a===aspect);});notify('Aspect preview changed. Save a production crop to create a versioned variant.');return;}
    const action=event.target.closest('[data-action]');if(action&&actions[action.dataset.action]){actions[action.dataset.action]();return;}
    if(!$('#tool-popover').hidden&&!event.target.closest('#tool-popover')&&!event.target.closest('#tool-dock'))closePopover();
  });
  document.addEventListener('input',event=>{
    const el=event.target;
    if(el.id==='scrub'){state.position=Number(el.value);updateTime();}
    if(el.id==='caption-size'){state.captionSize=Number(el.value);$('#caption-size-value').textContent=state.captionSize+'%';renderCaption();}
    if(el.id==='caption-copy'){state.captionCopy=el.value;renderCaption();}
    if(el.id==='audio-level')$('#audio-value').textContent=(Number(el.value)>0?'+':'')+el.value+' dB';
    if(el.id==='cut-range'){state.offset=Number(el.value);const t=time(1911+state.offset/1000,true);el.setAttribute('aria-valuetext',t);document.querySelectorAll('.cut-time').forEach(c=>c.textContent=t);$('#cut-blade').style.left=50+state.offset/20+'%';document.querySelector('[data-action="commit-cut"]').disabled=state.offset===state.appliedOffset;}
  });
  document.addEventListener('change',event=>{
    if(event.target.id==='caption-on'){state.captions=event.target.checked;renderCaption();}
    if(event.target.id==='safe-toggle'){state.safe=event.target.checked;$('#safe-zone').hidden=!state.safe;}
    if(event.target.id==='clean-audio'||event.target.id==='enhance-voice')notify('Audio control preview only. This sample has no audio track.');
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'){closeDialog();closePopover();if(state.focus)actions.focus();return;}
    if((event.metaKey||event.ctrlKey)&&event.key==='k'){event.preventDefault();actions.command();return;}
    if(!$('#dialog-backdrop').hidden&&event.key==='Tab'){
      const all=[...$('#dialog-backdrop').querySelectorAll('button:not(:disabled),input,textarea,select,a[href]')];
      if(event.shiftKey&&document.activeElement===all[0]){event.preventDefault();all.at(-1)?.focus();}
      else if(!event.shiftKey&&document.activeElement===all.at(-1)){event.preventDefault();all[0]?.focus();}
    }
    if(event.target.matches('input,textarea,select'))return;
    if(event.altKey&&/^[1-3]$/.test(event.key)){event.preventDefault();setMode(['edit','review','feedback'][Number(event.key)-1]);}
    const drag=event.target.closest('[data-drag]');
    if(drag&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();const node=graph.nodes.find(n=>n.id===drag.dataset.drag);const amount=event.shiftKey?25:5;node.x+=event.key==='ArrowRight'?amount:event.key==='ArrowLeft'?-amount:0;node.y+=event.key==='ArrowDown'?amount:event.key==='ArrowUp'?-amount:0;const card=drag.closest('[data-node]');card.style.left=node.x+'px';card.style.top=node.y+'px';drawWires();}
  });
  $('#dialog-backdrop').addEventListener('click',event=>{if(event.target===$('#dialog-backdrop'))closeDialog();});
  $('#canvas-viewport').addEventListener('pointerdown',event=>{
    const grip=event.target.closest('[data-drag]');
    if(!grip&&event.target.closest('.graph-node'))return;
    if(grip){const node=graph.nodes.find(n=>n.id===grip.dataset.drag);graphDrag={kind:'node',node,x:event.clientX,y:event.clientY,startX:node.x,startY:node.y};}
    else{graphDrag={kind:'pan',x:event.clientX,y:event.clientY,startX:graph.x,startY:graph.y};$('#canvas-viewport').classList.add('panning');}
    $('#canvas-viewport').setPointerCapture(event.pointerId);
  });
  $('#canvas-viewport').addEventListener('pointermove',event=>{
    if(!graphDrag)return;
    const dx=event.clientX-graphDrag.x,dy=event.clientY-graphDrag.y;
    if(graphDrag.kind==='node'){const n=graphDrag.node;n.x=graphDrag.startX+dx/graph.scale;n.y=graphDrag.startY+dy/graph.scale;const el=document.querySelector(`[data-node="${n.id}"]`);el.style.left=n.x+'px';el.style.top=n.y+'px';drawWires();}
    else{graph.x=graphDrag.startX+dx;graph.y=graphDrag.startY+dy;graphTransform();}
  });
  function releaseDrag(){graphDrag=null;$('#canvas-viewport').classList.remove('panning');}
  $('#canvas-viewport').addEventListener('pointerup',releaseDrag);$('#canvas-viewport').addEventListener('pointercancel',releaseDrag);
  new ResizeObserver(()=>{if(state.view==='canvas')fitGraph();}).observe($('#room'));
  hydrate();renderDock();renderCompass();renderPanel();renderReel();renderCaption();updateTime();
})();
