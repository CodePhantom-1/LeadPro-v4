
const S={page:'dashboard',leads:{data:[],total:0,offset:0,limit:100},sel:new Set(),waSel:new Set(),jobs:{},charts:{},wuLogsOffset:0,token:localStorage.getItem('token')||''};

// Attach JWT, throw readable errors, tolerate non-JSON (204, empty).
async function API(p, o){
  o = o || {};
  o.headers = Object.assign({}, o.headers || {});
  if (S.token) o.headers['Authorization'] = 'Bearer ' + S.token;
  const r = await fetch(p, o);
  if (r.status === 401 || r.status === 403) {
    S.token = '';
    try { localStorage.removeItem('token'); } catch(e){}
    showLoginModal();
    throw new Error('Authentication required');
  }
  if (!r.ok) {
    let detail = '';
    try { const j = await r.json(); detail = j.detail || j.error || ''; } catch(e){}
    throw new Error(`HTTP ${r.status}${detail ? ' — ' + detail : ''}`);
  }
  if (r.status === 204) return null;
  const text = await r.text();
  if (!text) return null;
  try { return JSON.parse(text); } catch(e){ return text; }
}

function showLoginModal(){
  openModal(`
    <h3 style="font-family:var(--cond);font-size:17px;margin-bottom:14px">Sign in</h3>
    <div style="font-size:12px;color:var(--text-2);margin-bottom:14px">Enter your LeadPro credentials. Default: <strong>admin</strong> with the password printed at startup.</div>
    <div class="form-row" style="margin-bottom:8px">
      <div class="form-group" style="flex:1"><label>Username</label><input type="text" id="login-username" value="admin" style="width:100%"></div>
    </div>
    <div class="form-row" style="margin-bottom:14px">
      <div class="form-group" style="flex:1"><label>Password</label><input type="password" id="login-password" style="width:100%" onkeydown="if(event.key==='Enter')doLogin()"></div>
    </div>
    <div id="login-err" style="color:var(--danger);font-size:12px;margin-bottom:10px;display:none"></div>
    <div style="display:flex;gap:8px">
      <button class="btn btn-primary" onclick="doLogin()">Sign in</button>
    </div>
  `);
  setTimeout(()=>{const el=document.getElementById('login-password');if(el)el.focus();},60);
}

async function doLogin(){
  const u=document.getElementById('login-username').value.trim();
  const p=document.getElementById('login-password').value;
  const err=document.getElementById('login-err');
  err.style.display='none';
  try {
    const r = await fetch('/api/auth/login',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:u,password:p}),
    });
    if (!r.ok) {
      let msg='Invalid credentials';
      try { const j=await r.json(); msg=j.detail||msg; } catch(e){}
      err.textContent=msg; err.style.display='block'; return;
    }
    const data=await r.json();
    S.token=data.access_token;
    try { localStorage.setItem('token', S.token); } catch(e){}
    closeModal();
    testSmtp();
    loadDash();
  } catch(e){ err.textContent=e.message; err.style.display='block'; }
}

async function doLogout(){
  S.token='';
  try { localStorage.removeItem('token'); } catch(e){}
  showLoginModal();
}
const TITLES={dashboard:'Dashboard',leadgen:'Lead Generation',campaigns:'Campaigns',leads:'Lead Database',intel:'Intelligence Engine',outreach:'Outreach Engine',replies:'Reply Monitor',analytics:'Conversion Analytics',warmup:'Email Warmup',settings:'Configuration'};

// Theme management
const themeToggle = document.getElementById('theme-toggle');
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');
const storedTheme = localStorage.getItem('theme') || 'dark';
if (storedTheme === 'light') document.documentElement.classList.add('theme-light');
updateThemeIcon();
themeToggle.addEventListener('click', toggleTheme);

function toggleTheme() {
  const isLight = document.documentElement.classList.toggle('theme-light');
  localStorage.setItem('theme', isLight ? 'light' : 'dark');
  updateThemeIcon();
}

function updateThemeIcon() {
  const icon = themeToggle.querySelector('i');
  // In light mode, offer to switch to dark (show moon icon)
  // In dark mode, offer to switch to light (show sun icon)
  if (document.documentElement.classList.contains('theme-light')) {
    icon.className = 'fas fa-moon';
    themeToggle.title = 'Switch to dark theme';
  } else {
    icon.className = 'fas fa-sun';
    themeToggle.title = 'Switch to light theme';
  }
}

function toggleAdvancedFilters(){
  const el=document.getElementById('lg-advanced-filters');
  const btn=document.getElementById('lg-advanced-toggle');
  const open=el.style.display==='none'||el.style.display==='';
  el.style.display=open?'block':'none';
  btn.textContent=open?'Advanced Filters ▲':'Advanced Filters ▼';
}

document.querySelectorAll('.nav-item').forEach(el=>el.addEventListener('click',()=>nav(el.dataset.page)));
function nav(p){S.page=p;document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.page===p));document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));document.getElementById('page-'+p).classList.add('active');document.getElementById('page-title').textContent=TITLES[p]||p;
if(p==='dashboard')loadDash();
if(p==='campaigns')loadCamps();
if(p==='leads'){loadLeads(0);loadFilters()}
if(p==='outreach'){loadOutCamps();switchOutTab('initial')}
if(p==='settings')loadSettings();
if(p==='analytics')loadAnalytics();
if(p==='warmup'){loadWuAccts();switchWuTab('run')}
if(p==='intel')loadIntelStats()}

function updateClock(){document.getElementById('time-badge').textContent=new Date().toLocaleTimeString('en-GB')}setInterval(updateClock,1000);updateClock();
async function testSmtp(){try{const r=await API('/api/smtp/test');const d=document.getElementById('smtp-dot');d.className=r.ok?'ok':'fail'}catch{}}

// ── Dashboard ──
async function loadDash(){try{const s=await API('/api/stats');$('s-total',fN(s.total_leads));$('s-hot',fN(s.high_score_leads));$('s-intent',fN(s.hot_intent||0));$('s-loss',fN(s.total_monthly_loss||0));$('s-emailed',fN(s.emailed));$('s-opened',fN(s.opened));$('s-open-rate',formatPercent(s.open_rate));$('s-replied',fN(s.replied));$('s-rate',formatPercent(s.reply_rate));$('s-bounced',fN(s.bounced));$('s-optout',fN(s.opted_out));$('total-badge',fN(s.total_leads)+' leads');renderSvcChart(s.by_service||[])}catch(e){console.error(e)}loadActivity()}

function renderSvcChart(d){const ctx=document.getElementById('service-chart').getContext('2d');if(S.charts.svc)S.charts.svc.destroy();S.charts.svc=new Chart(ctx,{type:'bar',data:{labels:d.map(x=>(x.ideal_service||'?').substring(0,18)),datasets:[{data:d.map(x=>x.n),backgroundColor:'rgba(14,165,233,0.2)',borderColor:'#0ea5e9',borderWidth:1,borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#94a3b8',font:{size:10},maxRotation:45},grid:{color:'#334155'}},y:{ticks:{color:'#94a3b8'},grid:{color:'#334155'}}}}})}

async function loadActivity(){try{const d=await API('/api/activity?limit=15');const f=document.getElementById('activity-feed');if(!d.length){f.innerHTML='<div class="empty"><p>No activity.</p></div>';return}const ic={emailed:'<i class="fas fa-envelope"></i>',replied:'<i class="fas fa-reply"></i>',opted_out:'<i class="fas fa-ban"></i>',scraped:'<i class="fas fa-search"></i>',audit_viewed:'<i class="fas fa-chart-bar"></i>',lead_created:'<i class="fas fa-plus"></i>',campaign_created:'<i class="fas fa-rocket"></i>',intel_run:'<i class="fas fa-brain"></i>',audit_generated:'<i class="fas fa-file-alt"></i>',proposal_sent:'<i class="fas fa-paper-plane"></i>',bounced:'<i class="fas fa-mailbox"></i>',open:'<i class="fas fa-eye"></i>',click:'<i class="fas fa-link"></i>',status_changed:'<i class="fas fa-sync-alt"></i>',error:'<i class="fas fa-exclamation-circle"></i>'};f.innerHTML=d.map(a=>`<div class="activity-item"><div class="act-icon">${ic[a.event_type]||'<i class="fas fa-circle"></i>'}</div><div class="act-body"><div class="act-title">${esc(a.business_name||'?')} — ${esc(a.event_type)}</div><div class="act-meta">${esc(a.email||'')} · ${fD(a.created_at)}</div></div></div>`).join('')}catch{}}

// ── Lead Gen ──
async function startLeadGen(){
  const c=document.getElementById('lg-country').value.trim();
  const t=parseInt(document.getElementById('lg-target').value)||200;
  if(!c){alert('Enter a target country.');return}

  const industry=document.getElementById('lg-industry').value.trim()||null;
  const business_type=document.getElementById('lg-business-type').value||null;
  const city=document.getElementById('lg-city').value.trim()||null;
  const mls=document.getElementById('lg-min-lead-score').value;
  const mos=document.getElementById('lg-min-ops-score').value;
  const mis=document.getElementById('lg-min-intent-score').value;
  const min_lead_score=mls?parseInt(mls):null;
  const min_ops_score=mos?parseInt(mos):null;
  const min_intent_score=mis?parseInt(mis):null;
  const exclude_competitors=document.getElementById('lg-exclude-competitors').checked;
  const include_clean_leads=document.getElementById('lg-include-clean').checked;
  const tech_stack_filters=Array.from(document.getElementById('lg-tech-stack-filters').selectedOptions).map(o=>o.value);
  const source_selection=Array.from(document.getElementById('lg-source-selection').selectedOptions).map(o=>o.value);

  const payload={country:c,target:t,industry,business_type,city,min_lead_score,min_ops_score,min_intent_score,
    exclude_competitors,include_clean_leads,
    tech_stack_filters:tech_stack_filters.length?tech_stack_filters:null,
    source_selection:source_selection.length?source_selection:null};

  document.getElementById('lg-btn').disabled=true;
  document.getElementById('lg-stop').style.display='';
  document.getElementById('lg-progress').classList.add('visible');
  setProgress('lg-prog-fill','lg-prog-label','lg-prog-pct',0,t);
  clearLog('terminal');
  appendLog('terminal','info',`Country: ${c} | Target: ${t}`);
  if(industry) appendLog('terminal','info',`Niche filter: ${industry}`);
  if(business_type) appendLog('terminal','info',`Business type: ${business_type}`);
  if(city) appendLog('terminal','info',`City: ${city}`);
  if(min_lead_score!=null) appendLog('terminal','info',`Min lead score: ${min_lead_score}`);
  if(min_ops_score!=null) appendLog('terminal','info',`Min ops score: ${min_ops_score}`);
  if(min_intent_score!=null) appendLog('terminal','info',`Min intent score: ${min_intent_score}`);
  if(exclude_competitors) appendLog('terminal','info','Excluding competitor businesses');
  if(include_clean_leads) appendLog('terminal','info','Including leads with no gaps');
  if(tech_stack_filters.length) appendLog('terminal','info',`Tech gap filters: ${tech_stack_filters.join(', ')}`);
  if(source_selection.length) appendLog('terminal','info',`Sources: ${source_selection.join(', ')}`);
  else appendLog('terminal','info','Sources: all');

  try{
    const{job_id}=await API('/api/leadgen/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const es=new EventSource(`/api/leadgen/stream/${job_id}`);
    S.jobs.leadgen=es;
    es.onmessage=e=>{
      const m=JSON.parse(e.data);
      if(m.type==='done'){es.close();delete S.jobs.leadgen;document.getElementById('lg-btn').disabled=false;document.getElementById('lg-stop').style.display='none';document.getElementById('lg-progress').classList.remove('visible');loadDash();return}
      appendLog('terminal',m.type,m.message);
      if(m.type==='progress'&&m.count!=null)setProgress('lg-prog-fill','lg-prog-label','lg-prog-pct',m.count,t)
    };
    es.onerror=()=>{appendLog('terminal','error','Connection lost.');es.close();document.getElementById('lg-btn').disabled=false;document.getElementById('lg-stop').style.display='none'}
  }catch(e){appendLog('terminal','error',e.message);document.getElementById('lg-btn').disabled=false;document.getElementById('lg-stop').style.display='none'}
}

// ── Campaigns ──
async function loadCamps(){try{const d=await API('/api/campaigns');const b=document.getElementById('campaigns-body');if(!d.length){b.innerHTML='<tr><td colspan="7"><div class="empty"><p>No campaigns.</p></div></td></tr>';return}b.innerHTML=d.map(c=>`<tr><td><span class="badge badge-dim">#${c.id}</span></td><td><strong>${esc(c.name)}</strong></td><td>${esc(c.country||'—')}</td><td>${statusBadge(c.status)}</td><td style="font-family:var(--mono)">${fN(c.lead_count||0)}</td><td style="color:var(--text-2);font-size:12px">${fD(c.created_at)}</td><td><div style="display:flex;gap:4px"><button class="btn btn-outline btn-sm" onclick="pauseCamp(${c.id},'${c.status==='active'?'paused':'active'}')">${c.status==='active'?'<i class="fas fa-pause"></i>':'<i class="fas fa-play"></i>'}</button><button class="btn btn-sm" style="background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.3);color:#ef4444" onclick="deleteCampaign(${c.id})"><i class="fas fa-trash-alt"></i></button></div></td></tr>`).join('')}catch{}}
function toggleNewCampaign(){const f=document.getElementById('new-campaign-form');f.style.display=f.style.display==='none'?'':'none';if(f.style.display!=='none')loadLeadBatches()}
async function loadLeadBatches(){try{const d=await API('/api/lead-batches');const s=document.getElementById('nc-lead-batch');s.innerHTML='<option value="__all_unassigned__">All Unassigned Leads</option>'+d.map(b=>`<option value="${esc(b.batch)}">${esc(b.batch)} (${fN(b.count)} leads)</option>`).join('')}catch(e){document.getElementById('nc-lead-batch').innerHTML='<option value="">Error loading batches</option>'}}
async function createCampaign(){const n=document.getElementById('nc-name').value.trim(),c=document.getElementById('nc-country').value.trim(),batch=document.getElementById('nc-lead-batch').value;if(!n||!c)return alert('Name + country required.');const body={name:n,country:c};if(batch)body.lead_batch=batch;await API('/api/campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});document.getElementById('nc-name').value='';document.getElementById('nc-country').value='';toggleNewCampaign();loadCamps()}
async function pauseCamp(id,s){await API(`/api/campaigns/${id}/status?status=${s}`,{method:'PATCH'});loadCamps()}
async function deleteCampaign(id) {
    if (!confirm('Delete this campaign? Leads will be unassigned but outreach history kept.')) return;
    try {
        await API(`/api/campaigns/${id}`, { method: 'DELETE' });
        loadCamps();
    } catch(e) {
        alert('Error: ' + e.message);
    }
}


// ── Leads ──
async function loadFilters(){try{const{countries,services}=await API('/api/leads/filters');const cs=document.getElementById('f-country'),ss=document.getElementById('f-service');const pc=cs.value,ps=ss.value;cs.innerHTML='<option value="">All Countries</option>'+countries.map(c=>`<option value="${esc(c)}"${c===pc?' selected':''}>${esc(c)}</option>`).join('');ss.innerHTML='<option value="">All Services</option>'+services.map(s=>`<option value="${esc(s)}"${s===ps?' selected':''}>${esc(s)}</option>`).join('')}catch{}}

async function loadLeads(o){S.leads.offset=o;const ms=document.getElementById('f-score').value||0,co=document.getElementById('f-country').value,sv=document.getElementById('f-service').value,he=document.getElementById('f-email').value==='1',so=document.getElementById('f-sort').value||'lead_score';const p=new URLSearchParams({min_score:ms,country:co,service:sv,has_email:he?'true':'false',sort:so,limit:S.leads.limit,offset:o});try{const{leads,total}=await API('/api/leads?'+p);S.leads.data=leads;S.leads.total=total;$('leads-count-label',fN(total)+' leads');renderLeads(leads);renderPager('leads-pager',o,S.leads.limit,total,loadLeads)}catch(e){console.error(e)}}

function renderLeads(leads){const b=document.getElementById('leads-body');if(!leads.length){b.innerHTML='<tr><td colspan="12"><div class="empty"><p>No leads.</p></div></td></tr>';return}
b.innerHTML=leads.map(l=>{const pp=tryJ(l.pain_points,[]);const sc=l.lead_score>=70?'var(--success)':l.lead_score>=50?'var(--warning)':'var(--text-2)';const ic=l.intent_score>=60?'var(--success)':l.intent_score>=35?'var(--warning)':'var(--text-3)';const loss=l.estimated_monthly_loss||0;const dm=l.decision_maker;const ts=tryJ(l.tech_stack_json,null);const gaps=ts===null?null:Object.entries(ts).filter(([k,v])=>v&&v[0]==='none').map(([k])=>k.replace(/_/g,' ')).slice(0,3);
return`<tr>
<td><div class="score-badge" style="background:${sc}20;color:${sc};border:1px solid ${sc}40">${l.lead_score}</div></td>
<td><div class="score-badge" style="background:${ic}20;color:${ic};border:1px solid ${ic}40">${l.intent_score||0}</div></td>
<td style="font-family:var(--mono);font-size:12px;color:${loss>2000?'var(--danger)':loss>500?'var(--warning)':'var(--text-2)'}">$${loss.toLocaleString()}</td>
<td><strong style="font-size:13px">${esc(l.business_name)}</strong></td>
<td style="font-size:12px">${dm?'<span style="color:var(--accent)">'+esc(dm)+'</span>':'<span style="color:var(--text-3)">—</span>'}</td>
<td style="font-family:var(--mono);font-size:11px;color:var(--text-2)">${esc(l.email||'—')}</td>
<td style="font-family:var(--mono);font-size:11px;color:var(--text-2)">${esc(l.phone||'—')}</td>
<td style="font-family:var(--mono);font-size:11px;color:var(--text-2)">${esc([l.city, l.country].filter(Boolean).join(', ') || '—')}</td>
<td><span class="badge badge-accent" style="font-size:10px">${esc(l.ideal_service||'—')}</span></td>
<td><div class="pain-list">${pp.slice(0,2).map(p=>'<span class="pain-tag">'+esc(p)+'</span>').join('')}</div></td>
<td style="font-size:11px">${ts===null?'<span style="color:var(--text-3)">?</span>':gaps.length?'<span style="color:var(--danger)"><i class="fas fa-times"></i> '+esc(gaps.join(', '))+'</span>':'<span style="color:var(--success)"><i class="fas fa-check"></i></span>'}</td>
      <td style="white-space:nowrap">
        <button class="btn btn-outline btn-sm" data-id="${l.id}" data-name="${esc(l.business_name)}" onclick="previewEmailModal(this.dataset.id, this.dataset.name)" title="Preview email"><i class="fas fa-envelope"></i></button>
        <button class="btn btn-outline btn-sm" onclick="genAuditPage(${l.id}, this)" title="Generate audit page"><i class="fas fa-chart-bar"></i></button>
        ${l.phone && l.phone !== 'N/A' && l.phone !== '' ? `<button class="btn btn-outline btn-sm" onclick="quickGenWa(${l.id},this)" title="Generate WhatsApp message"><i class="fas fa-comment-dots"></i></button>` : ''}
      </td>
</tr>`}).join('')}

async function previewEmailModal(id,n){
  openModal(`<h3 style="font-family:var(--cond);font-size:17px;margin-bottom:14px">Email Preview — ${esc(n)}</h3><div style="font-size:12px;color:var(--text-2);font-family:var(--mono)">Generating…</div>`);
  try{
    const r=await API(`/api/leads/${id}/preview-email`);
    if(r.error){document.getElementById('modal-content').innerHTML=`<div style="color:var(--danger)">${esc(r.error)}</div>`;return}
    _modalCopyText=(r.subject?'Subject: '+r.subject+'\n\n':'')+r.body;
    document.getElementById('modal-content').innerHTML=`
      <h3 style="font-family:var(--cond);font-size:17px;margin-bottom:14px">Email Preview — ${esc(n)}</h3>
      <div style="font-size:11px;color:var(--text-2);text-transform:uppercase;letter-spacing:.6px;margin-bottom:4px">Subject</div>
      <div class="modal-subject">${esc(r.subject||'—')}</div>
      <div style="font-size:11px;color:var(--text-2);text-transform:uppercase;letter-spacing:.6px;margin:12px 0 4px">Body</div>
      <div class="modal-body">${esc(r.body||'—')}</div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px">
        <span style="font-size:11px;color:var(--text-3);font-family:var(--mono)">Model: ${esc(r.model_used||'—')}</span>
        <button class="btn btn-outline btn-sm" onclick="copyModalText(this)">Copy</button>
      </div>`;
  }catch(e){document.getElementById('modal-content').innerHTML=`<div style="color:var(--danger)">${esc(e.message)}</div>`}
}

async function genAuditPage(id, btn){const originalHTML=btn?.innerHTML;if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-sync-alt fa-spin"></i>'};try{const r=await API('/api/leads/'+id+'/audit-page',{method:'POST'});if(r.url)window.open(r.url,'_blank')}catch(e){alert('Error: '+e.message)}finally{if(btn){btn.disabled=false;btn.innerHTML=originalHTML}}}

async function quickGenWa(id, btn){
  const originalHTML=btn?.innerHTML;
  if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-sync-alt fa-spin"></i>'}
  try{
    const r=await API('/api/whatsapp/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:id})});
    _modalCopyText=r.message||'';
    const phone=r.phone?r.phone.replace(/\D/g,''):'';
    openModal(`<h3 style="font-family:var(--cond);font-size:17px;margin-bottom:4px">WhatsApp Message</h3>
      <div style="font-size:12px;color:var(--text-2);margin-bottom:14px">${esc(r.business_name)}${r.phone?' · '+esc(r.phone):''}</div>
      <div style="font-size:13px;line-height:1.7;white-space:pre-wrap;background:var(--surface);padding:14px;border-radius:6px;border:1px solid var(--border)">${esc(r.message)}</div>
      <div style="margin-top:14px;display:flex;gap:8px">
        <button class="btn btn-outline btn-sm" onclick="copyModalText(this)">Copy</button>
        ${phone?`<a href="https://wa.me/${phone}" target="_blank" class="btn btn-success btn-sm" style="text-decoration:none">Open in WhatsApp</a>`:''}
      </div>`);
  }catch(e){alert('WhatsApp generation failed: '+e.message)}
  finally{if(btn){btn.disabled=false;btn.innerHTML=originalHTML}}
}
async function exportLeads(btn){
  const originalHTML=btn?.innerHTML;
  if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-sync-alt fa-spin"></i> Exporting…'}
  try{
    const minScore=document.getElementById('f-score').value||0;
    const country=document.getElementById('f-country').value||'';
    const service=document.getElementById('f-service').value||'';
    const sort=document.getElementById('f-sort').value||'lead_score';
    const params=new URLSearchParams({limit:10000,min_score:minScore,country,service,sort});
    const headers={};
    if (S.token) headers['Authorization']='Bearer '+S.token;
    const resp=await fetch('/api/leads?'+params,{headers});
    if(resp.status===401||resp.status===403){showLoginModal();return;}
    if(!resp.ok)throw new Error(`Export failed: ${resp.status}`);
    const data=await resp.json();
    const rows=data.leads||[];
    if(!rows.length){alert('No leads to export.');return}
    const cols=['id','business_name','email','phone','website','city','country','lead_score','ops_score','intent_score','estimated_monthly_loss','decision_maker','ideal_service','rating','review_count'];
    const csv=[cols.join(','),...rows.map(r=>cols.map(c=>{const v=r[c]??'';return'"'+String(v).replace(/"/g,'""')+'"'}).join(','))].join('\n');
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=`leads_${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a);a.click();document.body.removeChild(a);URL.revokeObjectURL(a.href);
  }catch(e){alert('Export error: '+e.message)}
  finally{if(btn){btn.disabled=false;btn.innerHTML=originalHTML||'Export'}}
}

// ── Intelligence ──
async function loadIntelStats(){try{const a=await API('/api/analytics');const s=a.seo_overview||{};$('seo-tracked',fN(s.tracked||0));$('seo-p1',fN(s.page_one||0));$('seo-miss',fN(s.not_ranking||0));$('seo-avg',s.avg_pos||'—')}catch{}}

async function startIntel(){
    const m=document.getElementById('intel-min').value||50;
    const country=document.getElementById('intel-country').value.trim()||null;
    const service=document.getElementById('intel-service').value.trim()||null;
    document.getElementById('intel-btn').disabled=true;
    document.getElementById('intel-stop').style.display='';
    document.getElementById('intel-progress').classList.add('visible');
    clearLog('intel-terminal');
    appendLog('intel-terminal','info','Starting intelligence run…');
    try{
        const body={min_score:+m};
        if(country) body.country=country;
        if(service) body.service=service;
        const{job_id}=await API('/api/intel/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const es=new EventSource(`/api/intel/stream/${job_id}`);
        S.jobs.intel=es;
        es.onmessage=e=>{
            const msg=JSON.parse(e.data);
            if(msg.type==='done'){
                es.close();
                delete S.jobs.intel;
                document.getElementById('intel-btn').disabled=false;
                document.getElementById('intel-stop').style.display='none';
                document.getElementById('intel-progress').classList.remove('visible');
                loadIntelStats();
                return
            }
            appendLog('intel-terminal',msg.type,msg.message);
            if(msg.progress!=null){
                const pct=msg.progress;
                document.getElementById('intel-prog-fill').style.width=pct+'%';
                document.getElementById('intel-prog-pct').textContent=pct+'%';
                if(msg.count!=null&&msg.total!=null)document.getElementById('intel-prog-label').textContent=msg.count+' / '+msg.total
            }
        };
        es.onerror=()=>{
            es.close();
            document.getElementById('intel-btn').disabled=false;
            document.getElementById('intel-stop').style.display='none';
            document.getElementById('intel-progress').classList.remove('visible')
        }
    }catch(e){
        appendLog('intel-terminal','error',e.message);
        document.getElementById('intel-btn').disabled=false;
        document.getElementById('intel-stop').style.display='none'
    }
}

async function genProposal(){const id=document.getElementById('prop-id').value;if(!id)return alert('Enter lead ID');const el=document.getElementById('prop-status');el.textContent='Generating PDF…';try{const r=await API(`/api/proposals/generate/${id}`,{method:'POST'});el.innerHTML=`<i class="fas fa-check"></i> <a href="/api/proposals/${id}/download" target="_blank">${esc(r.path)}</a>`}catch(e){el.textContent='<i class="fas fa-times"></i> '+e.message}}
async function genAuditPageById(btn){const id=document.getElementById('prop-id').value;if(!id)return alert('Enter lead ID');const el=document.getElementById('prop-status');el.textContent='Generating audit page…';const originalHTML=btn?.innerHTML;const originalDisabled=btn?.disabled;if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-sync-alt fa-spin"></i>'};try{const r=await API(`/api/leads/${id}/audit-page`,{method:'POST'});const safeUrl=encodeURI(r.url);el.innerHTML=`<i class="fas fa-check"></i> <a href="${safeUrl}" target="_blank">Open audit page</a>`}catch(e){el.innerHTML=`<i class="fas fa-times"></i> ${esc(e.message)}`}finally{if(btn){btn.disabled=originalDisabled;btn.innerHTML=originalHTML}}}
async function batchProposals(){const el=document.getElementById('prop-status');el.textContent='Generating batch…';try{const r=await API('/api/proposals/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({min_score:60,limit:20})});el.innerHTML=`<i class="fas fa-check"></i> Generated ${r.generated} proposals.`}catch(e){el.innerHTML=`<i class="fas fa-times"></i> ${esc(e.message)}`}}
async function cleanupAuditPages(btn){const originalHTML=btn?.innerHTML;const originalDisabled=btn?.disabled;if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-sync-alt fa-spin"></i>'};const el=document.getElementById('prop-status');el.textContent='Cleaning up expired audit pages…';try{const r=await API('/api/audit/cleanup',{method:'POST'});el.innerHTML=`<i class="fas fa-check"></i> ${esc(r.message)}`}catch(e){el.innerHTML=`<i class="fas fa-times"></i> ${esc(e.message)}`}finally{if(btn){btn.disabled=originalDisabled;btn.innerHTML=originalHTML}}}

// Store AI text for copying from modal
let _modalCopyText = '';
function copyModalText(btn){
  navigator.clipboard.writeText(_modalCopyText).then(()=>{btn.textContent='Copied!';setTimeout(()=>btn.textContent='Copy',1500)}).catch(()=>{
    const ta=document.createElement('textarea');ta.value=_modalCopyText;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);btn.textContent='Copied!';setTimeout(()=>btn.textContent='Copy',1500);
  });
}

async function genExecSummary(){
  const id=document.getElementById('prop-id').value;
  if(!id)return alert('Enter a lead ID first.');
  const el=document.getElementById('prop-status');
  el.textContent='Generating executive summary…';
  try{
    const r=await API(`/api/leads/${id}/executive-summary`,{method:'POST'});
    _modalCopyText=r.summary||'';
    openModal(`<h3 style="font-family:var(--cond);font-size:17px;margin-bottom:14px">Executive Summary — ${esc(r.business_name)}</h3>
      <div style="font-size:13px;line-height:1.75;white-space:pre-wrap;color:var(--text);background:var(--surface);padding:14px;border-radius:6px;border:1px solid var(--border);max-height:60vh;overflow-y:auto">${esc(r.summary)}</div>
      <div style="margin-top:14px"><button class="btn btn-outline btn-sm" onclick="copyModalText(this)">Copy</button></div>`);
    el.textContent='';
  }catch(e){el.textContent='Error: '+e.message}
}

async function genRecommendations(){
  const id=document.getElementById('prop-id').value;
  if(!id)return alert('Enter a lead ID first.');
  const el=document.getElementById('prop-status');
  el.textContent='Generating recommendations…';
  try{
    const r=await API(`/api/leads/${id}/recommendations`,{method:'POST'});
    _modalCopyText=r.recommendations||'';
    openModal(`<h3 style="font-family:var(--cond);font-size:17px;margin-bottom:14px">Recommendations — ${esc(r.business_name)}</h3>
      <div style="font-size:13px;line-height:1.75;white-space:pre-wrap;color:var(--text);background:var(--surface);padding:14px;border-radius:6px;border:1px solid var(--border);max-height:60vh;overflow-y:auto">${esc(r.recommendations)}</div>
      <div style="margin-top:14px"><button class="btn btn-outline btn-sm" onclick="copyModalText(this)">Copy</button></div>`);
    el.textContent='';
  }catch(e){el.textContent='Error: '+e.message}
}

// ── Outreach ──
async function loadOutCamps(){try{const c=await API('/api/campaigns');document.getElementById('out-campaign').innerHTML='<option value="">Select…</option>'+c.filter(x=>x.status==='active').map(x=>`<option value="${x.id}">${esc(x.name)} (${esc(x.country)})</option>`).join('')}catch{}}

async function previewOutreach(){const c=document.getElementById('out-campaign').value,m=document.getElementById('out-min-score').value||40;if(!c)return alert('Select campaign.');const d=await API(`/api/outreach/preview/${c}?min_score=${m}`);S.sel=new Set();document.getElementById('out-preview-wrap').style.display='';$('out-preview-label',d.filter(l=>!l._skipped).length+' leads ready');
document.getElementById('out-preview-body').innerHTML=d.map(l=>{const loss=l.estimated_monthly_loss||0;const dm=l.decision_maker;return`<tr style="opacity:${l._skipped?.4:1}"><td><input type="checkbox" class="lead-cb" value="${l.id}" ${l._skipped?'disabled':''} onchange="toggleLead(${l.id},this)"></td><td><div class="score-badge" style="background:var(--border);color:var(--text)">${l.lead_score}</div></td><td style="font-family:var(--mono);font-size:12px;color:${loss>1000?'var(--danger)':'var(--text-2)'}">$${loss.toLocaleString()}</td><td>${esc(l.business_name)}${l._skipped?' <span style="font-size:10px;color:var(--danger)">[skip]</span>':''}</td><td style="font-size:12px;color:var(--accent)">${esc(dm||'—')}</td><td style="font-family:var(--mono);font-size:11px;color:var(--text-2)">${esc(l.email||'—')}</td><td><button class="btn btn-outline btn-sm" onclick="previewEmailModal(${l.id},'${esc(l.business_name)}')">✉</button></td></tr>`}).join('');
document.getElementById('out-send-btn').disabled=false}
function toggleLead(id,cb){cb.checked?S.sel.add(id):S.sel.delete(id)}
function selectAllLeads(){document.querySelectorAll('.lead-cb:not(:disabled)').forEach(cb=>{cb.checked=true;S.sel.add(+cb.value)})}
function clearLeadSelection(){document.querySelectorAll('.lead-cb').forEach(cb=>{cb.checked=false});S.sel.clear()}
function toggleAllLeads(m){document.querySelectorAll('.lead-cb:not(:disabled)').forEach(cb=>{cb.checked=m.checked;m.checked?S.sel.add(+cb.value):S.sel.delete(+cb.value)})}

async function startOutreach(){const c=document.getElementById('out-campaign').value,m=document.getElementById('out-min-score').value||40;if(!c)return alert('Select campaign.');document.getElementById('out-send-btn').disabled=true;document.getElementById('out-stop-btn').style.display='';clearLog('out-terminal');appendLog('out-terminal','info','Starting…');try{const{job_id}=await API('/api/outreach/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({campaign_id:+c,min_score:+m,lead_ids:S.sel.size?[...S.sel]:[]})});const es=new EventSource(`/api/outreach/stream/${job_id}`);S.jobs.outreach=es;es.onmessage=e=>{const msg=JSON.parse(e.data);if(msg.type==='done'){es.close();delete S.jobs.outreach;document.getElementById('out-stop-btn').style.display='none';document.getElementById('out-send-btn').disabled=false;loadDash();return}appendLog('out-terminal',msg.type,msg.message)};es.onerror=()=>{es.close();document.getElementById('out-stop-btn').style.display='none';document.getElementById('out-send-btn').disabled=false}}catch(e){appendLog('out-terminal','error',e.message);document.getElementById('out-stop-btn').style.display='none';document.getElementById('out-send-btn').disabled=false}}

async function startFollowups(){clearLog('fu-terminal');appendLog('fu-terminal','info','Starting…');try{const{job_id}=await API('/api/followups/start',{method:'POST'});const es=new EventSource(`/api/outreach/stream/${job_id}`);S.jobs.followups=es;es.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='done'){es.close();delete S.jobs.followups;return}appendLog('fu-terminal',m.type,m.message)};es.onerror=()=>{es.close();delete S.jobs.followups}}catch(e){appendLog('fu-terminal','error',e.message)}}
function switchOutTab(t){
  document.querySelectorAll('.out-tab[data-tab]').forEach(x=>{
    const a=x.dataset.tab===t;
    x.style.color=a?'var(--accent)':'var(--text-2)';
    x.style.borderBottomColor=a?'var(--accent)':'transparent';
    x.style.borderBottom=a?'2px solid var(--accent)':'2px solid transparent';
  });
  document.getElementById('out-tab-initial').style.display=t==='initial'?'':'none';
  document.getElementById('out-tab-followup').style.display=t==='followup'?'':'none';
  document.getElementById('out-tab-whatsapp').style.display=t==='whatsapp'?'':'none';
  if(t==='whatsapp')loadWaLeads();
}

// ── Replies ──
async function startReplyScan(){clearLog('rep-terminal');appendLog('rep-terminal','info','Connecting…');try{const{job_id}=await API('/api/replies/scan',{method:'POST'});const es=new EventSource(`/api/replies/stream/${job_id}`);S.jobs.replyScan=es;es.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='done'){es.close();delete S.jobs.replyScan;return}appendLog('rep-terminal',m.type,m.message);if(m.type==='stats'&&m.data){const d=m.data;$('rep-scanned',d.scanned??'—');$('rep-replies',d.replies??'—');$('rep-optouts',d.opt_outs??'—');$('rep-errors',d.errors??'—');loadDash()}};es.onerror=()=>{es.close();delete S.jobs.replyScan}}catch(e){appendLog('rep-terminal','error',e.message)}}

// ── Analytics ──
async function loadAnalytics(){try{const a=await API('/api/analytics');
const fc=document.getElementById('funnel-container');const fn=a.funnel||{};const mx=Math.max(fn.total||1,1);
const steps=[{l:'Leads',v:fn.total||0,c:'var(--accent)'},{l:'Emailed',v:fn.emailed||0,c:'var(--accent)'},{l:'Opened',v:fn.opened||0,c:'var(--warning)'},{l:'Replied',v:fn.replied||0,c:'var(--success)'},{l:'Proposal',v:fn.proposal_sent||0,c:'#a855f7'}];
fc.innerHTML=steps.map(s=>`<div class="funnel-bar"><span class="funnel-label">${s.l}</span><div class="funnel-fill" style="width:${Math.max(s.v/mx*100,2)}%;background:${s.c}"></div><span class="funnel-val" style="color:${s.c}">${fN(s.v)}</span></div>`).join('');

document.getElementById('camp-perf-body').innerHTML=(a.campaign_perf||[]).map(c=>`<tr><td>${esc(c.campaign_name||'—')}</td><td>${fN(c.contacted)}</td><td>${fN(c.opens)}</td><td>${fN(c.replies)}</td><td>${fN(c.proposals)}</td></tr>`).join('')||'<tr><td colspan="5" style="color:var(--text-2);text-align:center">No data</td></tr>';
document.getElementById('qry-perf-body').innerHTML=(a.query_perf||[]).slice(0,15).map(q=>`<tr><td style="font-size:12px">${esc((q.source_query||'').substring(0,40))}</td><td>${fN(q.leads_found)}</td><td>${q.avg_score||'—'}</td><td>${fN(q.replies)}</td></tr>`).join('')||'<tr><td colspan="4" style="color:var(--text-2);text-align:center">No data</td></tr>';
if(a.bounce_stats){const bs=a.bounce_stats;document.getElementById('bounce-stats-body').innerHTML=[{t:'Total',v:bs.total||0},{t:'Hard',v:bs.hard||0},{t:'Soft',v:bs.soft||0}].map(r=>`<tr><td>${r.t}</td><td>${fN(r.v)}</td></tr>`).join('')||'<tr><td colspan="2">No data</td></tr>';}

const vd=a.daily_volume||[];const vctx=document.getElementById('vol-chart').getContext('2d');if(S.charts.vol)S.charts.vol.destroy();
S.charts.vol=new Chart(vctx,{type:'line',data:{labels:vd.map(d=>d.day),datasets:[{label:'Sent',data:vd.map(d=>d.sent),borderColor:'#0ea5e9',backgroundColor:'rgba(0,200,255,.1)',fill:true,tension:.3},{label:'Opened',data:vd.map(d=>d.opened),borderColor:'#ffd600',tension:.3},{label:'Replied',data:vd.map(d=>d.replied),borderColor:'#00e676',tension:.3}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#94a3b8',font:{size:11}}}},scales:{x:{ticks:{color:'#94a3b8',font:{size:10}},grid:{color:'#334155'}},y:{ticks:{color:'#94a3b8'},grid:{color:'#334155'}}}}});

const sp=a.service_perf||[];const sctx=document.getElementById('svc-reply-chart').getContext('2d');if(S.charts.svcR)S.charts.svcR.destroy();
S.charts.svcR=new Chart(sctx,{type:'bar',data:{labels:sp.map(s=>(s.service||'?').substring(0,16)),datasets:[{label:'Contacted',data:sp.map(s=>s.contacted),backgroundColor:'rgba(14,165,233,0.2)',borderColor:'#0ea5e9',borderWidth:1},{label:'Replies',data:sp.map(s=>s.replies),backgroundColor:'#00e67633',borderColor:'#00e676',borderWidth:1}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#94a3b8',font:{size:11}}}},scales:{x:{ticks:{color:'#94a3b8',font:{size:10},maxRotation:45},grid:{color:'#334155'}},y:{ticks:{color:'#94a3b8'},grid:{color:'#334155'}}}}});

const se=a.seo_overview||{};$('seo-tracked',fN(se.tracked||0));$('seo-p1',fN(se.page_one||0));$('seo-miss',fN(se.not_ranking||0));$('seo-avg',se.avg_pos||'—')}catch(e){console.error(e)}}

// ── Warmup ──
async function loadWuAccts(){
  try{
    const d=await API('/api/warmup/accounts');
    const b=document.getElementById('wu-accounts-body');
    // Show Brevo notice if Brevo SMTP login is configured
    const brevoNotice=document.getElementById('wu-brevo-notice');
    try{
      const cfg=await API('/api/config');
      if(brevoNotice)brevoNotice.style.display=cfg.BREVO_SMTP_LOGIN?'':'none';
    }catch(e){if(brevoNotice)brevoNotice.style.display='none';}
    if(!d.length){b.innerHTML='<tr><td colspan="7" style="color:var(--text-2);text-align:center;padding:24px">No accounts. Add a sender and at least one receiver to start warming up.</td></tr>';return}
    b.innerHTML=d.map(a=>{
      const promoteBtn=a.role==='sender'&&a.status==='ready'
        ?`<button class="btn btn-success btn-sm" onclick="promoteWuAcct(${a.id})" title="Promote to Outreach Account" style="font-size:10px">↑ Promote</button>`
        :'';
      return `<tr>
      <td style="font-family:var(--mono);font-size:12px">${esc(a.email)}</td>
      <td>${esc(a.domain)}</td>
      <td><span class="badge ${a.role==='sender'?'badge-accent':'badge-dim'}">${a.role}</span></td>
      <td style="font-family:var(--mono)">${a.current_day||0}/28</td>
      <td style="font-family:var(--mono)">${a.daily_limit||0}</td>
      <td><span class="badge ${a.status==='ready'?'badge-green':a.status==='warming'?'badge-warn':'badge-dim'}">${a.status}</span></td>
      <td style="white-space:nowrap">
        ${promoteBtn}
        <button class="btn btn-outline btn-sm" onclick="editWuAcct(${a.id})" title="Edit settings">⚙</button>
        <button class="btn btn-outline btn-sm" onclick="testWuAccountById(${a.id})" title="Test SMTP">⚡</button>
        <button class="btn btn-outline btn-sm" onclick="checkPlacementById(${a.id})" title="Check placement">📊</button>
        <button class="btn btn-outline btn-sm" onclick="rescueById(${a.id})" title="Rescue from spam">🛟</button>
        <button class="btn btn-danger btn-sm" onclick="delWuAcct(${a.id})" title="Delete">✗</button>
      </td>
    </tr>`}).join('')
  }catch(e){console.error(e)}
}

async function editWuAcct(id){
  openModal(`<h3 style="font-family:var(--cond);font-size:17px;margin-bottom:14px">Edit Warmup Account</h3><div>Loading…</div>`);
  try{
    const a=await API('/api/warmup/accounts/'+id);
    document.getElementById('modal-content').innerHTML=`
      <h3 style="font-family:var(--cond);font-size:17px;margin-bottom:4px">Edit — ${esc(a.email)}</h3>
      <div style="font-size:12px;color:var(--text-2);margin-bottom:16px">Update SMTP/IMAP credentials and warmup settings</div>
      <div class="form-row">
        <div class="form-group"><label>SMTP Host</label><input type="text" id="wu-edit-smtp-host" value="${esc(a.smtp_host||'smtp.gmail.com')}" style="width:180px"></div>
        <div class="form-group"><label>SMTP Port</label><input type="number" id="wu-edit-smtp-port" value="${a.smtp_port||587}" style="width:90px"></div>
        <div class="form-group"><label>IMAP Host</label><input type="text" id="wu-edit-imap-host" value="${esc(a.imap_host||'imap.gmail.com')}" style="width:180px"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Daily Limit</label><input type="number" id="wu-edit-daily-limit" value="${a.daily_limit||10}" style="width:90px"></div>
        <div class="form-group"><label>Status</label>
          <select id="wu-edit-status">
            <option value="warming" ${a.status==='warming'?'selected':''}>Warming</option>
            <option value="ready" ${a.status==='ready'?'selected':''}>Ready</option>
            <option value="paused" ${a.status==='paused'?'selected':''}>Paused</option>
          </select>
        </div>
        <div class="form-group"><label>Reset Day Counter</label>
          <select id="wu-edit-day">
            <option value="">Keep current (Day ${a.current_day||0})</option>
            <option value="0">Reset to Day 0</option>
          </select>
        </div>
      </div>
      <div style="margin-top:20px;display:flex;gap:8px">
        <button class="btn btn-primary" onclick="saveWuEdit(${a.id})">💾 Save</button>
        <button class="btn btn-outline" onclick="closeModal()">Cancel</button>
      </div>`;
  }catch(e){document.getElementById('modal-content').innerHTML+=`<div style="color:var(--danger);margin-top:8px">${e.message}</div>`}
}

async function saveWuEdit(id){
  const smtp_host=document.getElementById('wu-edit-smtp-host').value.trim();
  const smtp_port=parseInt(document.getElementById('wu-edit-smtp-port').value);
  const imap_host=document.getElementById('wu-edit-imap-host').value.trim();
  const daily_limit=parseInt(document.getElementById('wu-edit-daily-limit').value);
  const status=document.getElementById('wu-edit-status').value;
  const dayReset=document.getElementById('wu-edit-day').value;
  const payload={smtp_host,smtp_port,imap_host,daily_limit,status};
  if(dayReset==='0')payload.current_day=0;
  try{
    await API('/api/warmup/accounts/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    closeModal();loadWuAccts();appendLog('warmup-terminal','success','Account updated successfully.');
  }catch(e){alert('Update failed: '+e.message)}
}

async function addWarmupAcct(){
  const email=document.getElementById('wu-email').value.trim();
  const password=document.getElementById('wu-pass').value;
  const domain=document.getElementById('wu-domain').value.trim();
  const role=document.getElementById('wu-role').value;
  const smtp_host=document.getElementById('wu-smtp-host').value.trim()||'smtp.gmail.com';
  const smtp_port=parseInt(document.getElementById('wu-smtp-port').value)||587;
  const imap_host=document.getElementById('wu-imap-host').value.trim()||'imap.gmail.com';
  if(!email||!password||!domain)return alert('Email, password, and domain are required.');
  try{
    await API('/api/warmup/accounts',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email,password,domain,role,smtp_host,smtp_port,imap_host})});
    document.getElementById('wu-email').value='';
    document.getElementById('wu-pass').value='';
    document.getElementById('wu-domain').value='';
    document.getElementById('wu-smtp-host').value='';
    document.getElementById('wu-imap-host').value='';
    appendLog('warmup-terminal','success',`Added ${role} account: ${email}`);
    loadWuAccts();
  }catch(e){alert('Failed to add account: '+e.message)}
}

async function delWuAcct(id){
  if(!confirm('Remove this warmup account?'))return;
  try{await API(`/api/warmup/accounts/${id}`,{method:'DELETE'});loadWuAccts()}catch(e){alert(e.message)}
}

async function promoteWuAcct(id){
  if(!confirm('Promote this warmed-up account to an Outreach Account? It will be added with SMTP Password mode using its current credentials.'))return;
  try{
    const a=await API('/api/warmup/accounts/'+id);
    await API('/api/outreach-accounts',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        email:a.email,
        password:a.password||'',
        smtp_host:a.smtp_host||'smtp.gmail.com',
        smtp_port:a.smtp_port||587,
        imap_host:a.imap_host||'imap.gmail.com',
        display_name:a.email.split('@')[0],
        daily_limit:a.daily_limit||150,
        sending_mode:'smtp_password'
      })});
    appendLog('warmup-terminal','success','Promoted '+a.email+' to Outreach Account');
    loadWuAccts();
  }catch(e){alert('Promote failed: '+e.message)}
}

async function runWarmup(){
  clearLog('warmup-terminal');
  appendLog('warmup-terminal','info','Starting warmup cycle…');
  switchWuTab('run');
  try{
    const{job_id}=await API('/api/warmup/run',{method:'POST'});
    const es=new EventSource(`/api/warmup/stream/${job_id}`);
    es.onmessage=e=>{
      const m=JSON.parse(e.data);
      if(m.type==='done'){es.close();loadWuAccts();appendLog('warmup-terminal','success','Cycle complete.');return}
      appendLog('warmup-terminal',m.type,m.message)
    };
    es.onerror=()=>{es.close();appendLog('warmup-terminal','warning','Connection closed.')}
  }catch(e){appendLog('warmup-terminal','error',e.message)}
}

function switchWuTab(t){
  document.querySelectorAll('.out-tab[data-wu-tab]').forEach(x=>{const a=x.dataset.wuTab===t;x.style.color=a?'var(--accent)':'var(--text-2)';x.style.borderBottomColor=a?'var(--accent)':'transparent'});
  document.getElementById('wu-tab-run').style.display=t==='run'?'':'none';
  document.getElementById('wu-tab-placement').style.display=t==='placement'?'':'none';
  document.getElementById('wu-tab-logs').style.display=t==='logs'?'':'none';
  if(t==='placement')loadWarmupPlacement();
  if(t==='logs')loadWarmupLogs();
}

async function loadWarmupPlacement(){
  switchWuTab('placement');
  try{
    const r=await API('/api/warmup/placement-summary');
    document.getElementById('wu-total-sent').textContent=fN(r.total_sent||0);
    document.getElementById('wu-total-rescued').textContent=fN(r.total_rescued||0);
    document.getElementById('wu-domains-count').textContent=fN((r.by_domain||[]).length);
    document.getElementById('wu-domain-stats-body').innerHTML=(r.by_domain||[]).map(d=>
      `<tr><td style="font-family:var(--mono);font-size:12px">${esc(d.domain)}</td><td>${fN(d.sent)}</td><td>${fN(d.rescued||0)}</td></tr>`
    ).join('')||'<tr><td colspan="3" style="color:var(--text-2);text-align:center">No warmup emails sent yet</td></tr>';
  }catch(e){console.error(e)}
}

async function checkAccountPlacement(){
  const id=document.getElementById('wu-check-id').value;
  if(!id)return alert('Enter an account ID.');
  const el=document.getElementById('wu-placement-result');
  el.textContent='Checking…';
  try{
    const r=await API(`/api/warmup/accounts/${id}/placement`,{method:'POST'});
    const rate=((r.inbox_rate||0)*100).toFixed(0);
    const col=rate>=80?'var(--success)':rate>=50?'var(--warning)':'var(--danger)';
    el.innerHTML=`<span style="color:${col};font-size:14px;font-weight:600">${rate}% inbox rate</span> — ${fN(r.inbox)} inbox / ${fN(r.spam)} spam (${fN(r.total)} total)`;
  }catch(e){el.style.color='var(--danger)';el.textContent='Error: '+e.message}
}

async function checkPlacementById(id){
  appendLog('warmup-terminal','info',`Checking placement for account ${id}…`);
  switchWuTab('run');
  try{
    const r=await API(`/api/warmup/accounts/${id}/placement`,{method:'POST'});
    const rate=((r.inbox_rate||0)*100).toFixed(0);
    appendLog('warmup-terminal',rate>=80?'success':'warning',`Account ${id}: ${rate}% inbox (${r.inbox}i / ${r.spam}s)`);
  }catch(e){appendLog('warmup-terminal','error',`Placement check failed: ${e.message}`)}
}

async function rescueAccount(){
  const id=document.getElementById('wu-check-id').value;
  if(!id)return alert('Enter an account ID.');
  const el=document.getElementById('wu-placement-result');
  el.textContent='Rescuing emails from spam…';
  try{
    const r=await API(`/api/warmup/accounts/${id}/rescue`,{method:'POST'});
    el.innerHTML=`<span style="color:var(--success)">Rescued ${fN(r.rescued)} emails from spam for ${esc(r.account)}</span>`;
  }catch(e){el.style.color='var(--danger)';el.textContent='Error: '+e.message}
}

async function rescueById(id){
  appendLog('warmup-terminal','info',`Rescuing spam for account ${id}…`);
  switchWuTab('run');
  try{
    const r=await API(`/api/warmup/accounts/${id}/rescue`,{method:'POST'});
    appendLog('warmup-terminal','success',`Rescued ${r.rescued} emails from spam — ${r.account}`);
  }catch(e){appendLog('warmup-terminal','error',`Rescue failed: ${e.message}`)}
}

async function rescueAllSpam(btn){
  if(btn){btn.disabled=true;btn.textContent='Rescuing…'}
  appendLog('warmup-terminal','info','Running spam rescue on all receiver accounts…');
  switchWuTab('run');
  try{
    const r=await API('/api/warmup/rescue-all',{method:'POST'});
    appendLog('warmup-terminal','success',`Rescue complete: ${r.rescued} emails rescued across ${r.accounts_checked} accounts`);
  }catch(e){appendLog('warmup-terminal','error',e.message)}
  finally{if(btn){btn.disabled=false;btn.textContent='🛟 Rescue All from Spam'}}
}

async function testWuAccount(){
  const id=document.getElementById('wu-check-id').value;
  if(!id)return alert('Enter an account ID.');
  const el=document.getElementById('wu-placement-result');
  el.textContent='Testing SMTP connection…';
  try{
    const r=await API(`/api/warmup/accounts/${id}/test`,{method:'POST'});
    el.innerHTML=r.ok
      ?`<span style="color:var(--success)">✓ SMTP connection OK for ${esc(r.email)}</span>`
      :`<span style="color:var(--danger)">✗ SMTP failed: ${esc(r.error||'Unknown error')}</span>`;
  }catch(e){el.style.color='var(--danger)';el.textContent='Error: '+e.message}
}

async function testWuAccountById(id){
  appendLog('warmup-terminal','info',`Testing SMTP for account ${id}…`);
  switchWuTab('run');
  try{
    const r=await API(`/api/warmup/accounts/${id}/test`,{method:'POST'});
    appendLog('warmup-terminal',r.ok?'success':'error',r.ok?`✓ SMTP OK — ${r.email}`:`✗ SMTP failed — ${r.error}`);
  }catch(e){appendLog('warmup-terminal','error',e.message)}
}

async function loadWarmupLogs(){
  const limit=50;
  try{
    const r=await API(`/api/warmup/logs?limit=${limit}&offset=${S.wuLogsOffset}`);
    const b=document.getElementById('wu-logs-body');
    if(!r.logs.length){b.innerHTML='<tr><td colspan="5" style="color:var(--text-2);text-align:center;padding:24px">No warmup emails logged yet.</td></tr>';return}
    b.innerHTML=r.logs.map(l=>`<tr>
      <td style="font-family:var(--mono);font-size:11px;color:var(--text-2)">${fD(l.sent_at)}</td>
      <td style="font-size:12px">${esc(l.from_email||'—')}</td>
      <td style="font-size:12px">${esc(l.to_email||'—')}</td>
      <td style="font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(l.subject||'—')}</td>
      <td>${l.rescued?'<span class="badge badge-green">Yes</span>':'<span class="badge badge-dim">No</span>'}</td>
    </tr>`).join('');
    renderPager('wu-logs-pager',S.wuLogsOffset,limit,r.total,(o)=>{S.wuLogsOffset=o;loadWarmupLogs()});
  }catch(e){console.error(e)}
}
// ── WhatsApp ──
async function loadWaLeads(){
  const minScore=document.getElementById('wa-min-score').value||40;
  try{
    const leads=await API(`/api/whatsapp/leads?min_score=${minScore}&limit=200`);
    S.waSel=new Set();
    const b=document.getElementById('wa-leads-body');
    const wrap=document.getElementById('wa-leads-wrap');
    if(!leads || !leads.length){
      b.innerHTML='<tr><td colspan="7"><div class="empty"><p>No leads with phone numbers found at this score threshold.</p></div></td></tr>';
      wrap.style.display='';
      // Disable the "Generate Messages" button since there's nothing to generate
      const genBtn=document.getElementById('wa-gen-btn');
      if(genBtn)genBtn.disabled=true;
      updateWaSelCount();
      appendLog('whatsapp-terminal','warning',`No leads with phone numbers at score >= ${minScore}`);
      return
    }
    const genBtn=document.getElementById('wa-gen-btn');
    if(genBtn)genBtn.disabled=false;
    b.innerHTML=leads.map(l=>`<tr>
      <td><input type="checkbox" class="wa-cb" value="${l.id}" onchange="toggleWa(${l.id},this)"></td>
      <td><div class="score-badge" style="background:var(--border);color:var(--text)">${l.lead_score}</div></td>
      <td><strong>${esc(l.business_name)}</strong></td>
      <td style="font-family:var(--mono);font-size:12px">${esc(l.phone||'—')}</td>
      <td><span class="badge badge-accent" style="font-size:10px">${esc(l.ideal_service||'—')}</span></td>
      <td style="font-size:12px;color:var(--accent)">${esc(l.decision_maker||'—')}</td>
      <td><button class="btn btn-outline btn-sm" onclick="generateSingleWa(${l.id},'${esc(l.business_name)}')">Gen</button></td>
    </tr>`).join('');
    document.getElementById('wa-leads-wrap').style.display='';
    updateWaSelCount();
    appendLog('whatsapp-terminal','info',`Loaded ${leads.length} leads with phone numbers`);
  }catch(e){appendLog('whatsapp-terminal','error',e.message)}
}
function toggleWa(id,cb){cb.checked?S.waSel.add(id):S.waSel.delete(id);updateWaSelCount()}
function toggleAllWa(m){document.querySelectorAll('.wa-cb').forEach(cb=>{cb.checked=m.checked;m.checked?S.waSel.add(+cb.value):S.waSel.delete(+cb.value)});updateWaSelCount()}
function updateWaSelCount(){document.getElementById('wa-sel-count').textContent=S.waSel.size+' selected'}

async function generateSingleWa(id, name){
  appendLog('whatsapp-terminal','generating',`Generating for ${name}…`);
  try{
    const r=await API('/api/whatsapp/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:id})});
    if(!r.message){appendLog('whatsapp-terminal','error',`AI failed for ${name}`);return}
    appendLog('whatsapp-terminal','success',`${name}: ${r.message}`);
    showWaMessage(id, name, r.message, r.phone);
  }catch(e){appendLog('whatsapp-terminal','error',`${name}: ${e.message}`)}
}

async function generateWhatsAppBatch(){
  if(!S.waSel.size){alert('Select at least one lead.');return}
  const btn=document.getElementById('wa-gen-btn');
  btn.disabled=true;
  document.getElementById('wa-results-wrap').style.display='';
  document.getElementById('wa-messages-list').innerHTML='';
  const total=S.waSel.size;
  appendLog('whatsapp-terminal','info',`Generating ${total} messages (concurrency=4)…`);
  const ids=[...S.waSel];
  let done=0;
  const CONCURRENCY=4;
  async function worker(){
    while(ids.length){
      const id=ids.shift();
      try{
        const r=await API('/api/whatsapp/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:id})});
        if(r && r.message){showWaMessage(id,r.business_name,r.message,r.phone);appendLog('whatsapp-terminal','sent',`[OK] ${r.business_name}`)}
        else appendLog('whatsapp-terminal','warning',`AI returned empty for ID ${id}`);
      }catch(e){appendLog('whatsapp-terminal','error',`ID ${id}: ${e.message}`)}
      done++;
      appendLog('whatsapp-terminal','progress',`${done}/${total} complete`);
    }
  }
  await Promise.all(Array.from({length:Math.min(CONCURRENCY,total)},worker));
  btn.disabled=false;
  appendLog('whatsapp-terminal','summary',`Done. Generated ${done} messages.`);
}

function showWaMessage(id, name, message, phone){
  const list=document.getElementById('wa-messages-list');
  const card=document.createElement('div');
  card.style.cssText='background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:12px';
  const phoneDigits = phone ? String(phone).replace(/\D/g,'') : '';
  card.innerHTML=`
    <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
      <div>
        <strong style="font-size:13px">${esc(name||'Lead #'+id)}</strong>
        ${phone?`<span style="font-family:var(--mono);font-size:11px;color:var(--text-2);margin-left:8px">${esc(phone)}</span>`:''}
      </div>
      <button class="btn btn-outline btn-sm" data-copy-btn>Copy</button>
    </div>
    <div style="font-size:13px;line-height:1.6;color:var(--text);background:var(--card);padding:10px 12px;border-radius:4px;border:1px solid var(--border)">${esc(message)}</div>
    ${phoneDigits?`<div style="margin-top:8px"><a href="https://wa.me/${phoneDigits}" target="_blank" class="btn btn-success btn-sm" style="text-decoration:none">Open in WhatsApp</a></div>`:''}
  `;
  // Attach the raw message to the button directly (avoids HTML-entity round-trip bugs)
  const copyBtn = card.querySelector('[data-copy-btn]');
  if (copyBtn) {
    copyBtn._msg = message;
    copyBtn.addEventListener('click', () => copyWaMsg(copyBtn));
  }
  list.appendChild(card);
  list.scrollTop=list.scrollHeight;
}

function copyWaMsg(btn){
  const msg = btn._msg || '';
  const done = () => { btn.textContent='Copied!'; setTimeout(()=>btn.textContent='Copy',1500); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(msg).then(done).catch(()=>{
      const ta=document.createElement('textarea');ta.value=msg;document.body.appendChild(ta);ta.select();
      try{document.execCommand('copy');}finally{document.body.removeChild(ta);}
      done();
    });
  } else {
    const ta=document.createElement('textarea');ta.value=msg;document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');}finally{document.body.removeChild(ta);}
    done();
  }
}
function clearWaResults(){document.getElementById('wa-messages-list').innerHTML='';document.getElementById('wa-results-wrap').style.display='none'}

// ── Outreach Accounts ──
async function loadOutreachAccounts(){
  try{
    const accounts=await API('/api/outreach-accounts');
    const b=document.getElementById('oa-accounts-body');
    if(!accounts.length){
      b.innerHTML='<tr><td colspan="9" style="color:var(--text-2);text-align:center;padding:20px">No accounts added. Using single config account as fallback.</td></tr>';
      return;
    }
    b.innerHTML=accounts.map(a=>{
      const isBrevo=a.sending_mode==='brevo_relay';
      const modeBadge=isBrevo?'<span class="badge" style="background:rgba(33,150,243,.15);color:#64b5f6">Brevo</span>':'<span class="badge badge-dim">SMTP</span>';
      const smtpLabel=isBrevo?'Brevo Relay':(esc(a.smtp_host)+':'+a.smtp_port);
      return `<tr>
      <td style="font-family:var(--mono);font-size:12px">${esc(a.email)}</td>
      <td>${modeBadge}</td>
      <td style="font-size:12px">${esc(a.display_name||'—')}</td>
      <td style="font-family:var(--mono);font-size:11px;color:var(--text-2)">${smtpLabel}</td>
      <td style="font-family:var(--mono)">${fN(a.daily_limit)}</td>
      <td style="font-family:var(--mono);color:${a.sent_today>0?'var(--warning)':'var(--text-2)'}">${fN(a.sent_today)}</td>
      <td style="font-family:var(--mono);color:${a.remaining>50?'var(--success)':a.remaining>0?'var(--warning)':'var(--danger)'}">${fN(a.remaining)}</td>
      <td><span class="badge ${a.active?'badge-green':'badge-dim'}">${a.active?'Active':'Paused'}</span></td>
      <td style="white-space:nowrap">
        <button class="btn btn-outline btn-sm" onclick="testOA(${a.id})" title="Test">⚡</button>
        <button class="btn btn-outline btn-sm" onclick="editOA(${a.id})" title="Edit">⚙</button>
        <button class="btn btn-outline btn-sm" onclick="toggleOA(${a.id},${a.active?0:1})" title="${a.active?'Pause':'Activate'}">${a.active?'⏸':'▷'}</button>
        <button class="btn btn-danger btn-sm" onclick="deleteOA(${a.id})" title="Delete">✗</button>
      </td>
    </tr>`}).join('');
  }catch(e){console.error(e)}
}

function toggleOaMode(){
  const mode=document.getElementById('oa-sending-mode').value;
  const smtpFields=document.querySelectorAll('.oa-smtp-fields');
  const brevoFields=document.querySelectorAll('.oa-brevo-fields');
  const passGroup=document.getElementById('oa-pass-group');
  if(mode==='brevo_relay'){
    smtpFields.forEach(el=>el.style.display='none');
    brevoFields.forEach(el=>el.style.display='');
    if(passGroup)passGroup.style.display='none';
    document.getElementById('oa-email').placeholder='sarah@outreach.yourdomain.com';
  }else{
    smtpFields.forEach(el=>el.style.display='');
    brevoFields.forEach(el=>el.style.display='none');
    if(passGroup)passGroup.style.display='';
    document.getElementById('oa-email').placeholder='you@yourdomain.com';
  }
}

async function testBrevo(){
  try{
    const r=await API('/api/brevo/test');
    if(r.ok) alert('✓ Brevo SMTP connection successful');
    else alert('✗ Brevo SMTP failed — check your SMTP Login and Key in settings');
  }catch(e){alert('Brevo test failed: '+e.message)}
}

async function addOutreachAccount(){
  const email=document.getElementById('oa-email').value.trim();
  const sending_mode=document.getElementById('oa-sending-mode').value;
  const password=sending_mode==='brevo_relay'?'':document.getElementById('oa-pass').value;
  const display_name=document.getElementById('oa-name').value.trim()||null;
  const smtp_host=document.getElementById('oa-smtp-host').value.trim()||'smtp.gmail.com';
  const smtp_port=parseInt(document.getElementById('oa-smtp-port').value)||587;
  const imap_host=document.getElementById('oa-imap-host').value.trim()||'imap.gmail.com';
  const daily_limit=parseInt(document.getElementById('oa-daily-limit').value)||150;
  const signature=document.getElementById('oa-signature').value.trim()||null;
  if(!email)return alert('Email is required.');
  if(sending_mode==='smtp_password'&&!password)return alert('Password is required for SMTP mode.');
  try{
    await API('/api/outreach-accounts',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email,password,smtp_host,smtp_port,imap_host,display_name,daily_limit,sending_mode,signature})});
    document.getElementById('oa-email').value='';
    document.getElementById('oa-pass').value='';
    document.getElementById('oa-name').value='';
    document.getElementById('oa-smtp-host').value='';
    document.getElementById('oa-imap-host').value='';
    document.getElementById('oa-signature').value='';
    loadOutreachAccounts();
  }catch(e){alert('Failed to add account: '+e.message)}
}

async function deleteOA(id){
  if(!confirm('Remove this outreach account?'))return;
  try{await API('/api/outreach-accounts/'+id,{method:'DELETE'});loadOutreachAccounts()}catch(e){alert(e.message)}
}

async function toggleOA(id, active){
  try{await API('/api/outreach-accounts/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({active})});loadOutreachAccounts()}catch(e){alert(e.message)}
}

async function testOA(id){
  try{
    const r=await API('/api/outreach-accounts/'+id+'/test',{method:'POST'});
    const mode=r.mode==='brevo_relay'?' (Brevo)':' (SMTP)';
    if(r.ok) alert('✓ SMTP connection successful for '+r.email+mode);
    else alert('✗ SMTP failed for '+r.email+mode+': '+(r.error||'unknown error'));
  }catch(e){alert('Test failed: '+e.message)}
}

async function editOA(id){
  try{
    const r=await API('/api/outreach-accounts');
    const a=r.find(x=>x.id===id);
    if(!a)return;
    const isBrevo=a.sending_mode==='brevo_relay';
    openModal(`<h3 style="font-family:var(--cond);font-size:17px;margin-bottom:14px">Edit — ${esc(a.email)}</h3>
      <div class="form-row"><div class="form-group">
        <label>Sending Mode</label>
        <select id="oa-edit-mode" onchange="toggleOaEditMode()" style="width:180px;padding:6px 8px;border-radius:var(--r);border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:13px">
          <option value="smtp_password" ${!isBrevo?'selected':''}>SMTP Password</option>
          <option value="brevo_relay" ${isBrevo?'selected':''}>Brevo Relay</option>
        </select>
      </div>
      <div class="form-group"><label>Display Name</label><input type="text" id="oa-edit-name" value="${esc(a.display_name||'')}" style="width:200px"></div></div>
      <div class="form-row oa-edit-smtp-fields" style="${isBrevo?'display:none':''}">
        <div class="form-group"><label>SMTP Host</label><input type="text" id="oa-edit-smtp-host" value="${esc(a.smtp_host||'smtp.gmail.com')}" style="width:180px"></div>
        <div class="form-group"><label>SMTP Port</label><input type="number" id="oa-edit-smtp-port" value="${a.smtp_port||587}" style="width:80px"></div>
        <div class="form-group"><label>IMAP Host</label><input type="text" id="oa-edit-imap-host" value="${esc(a.imap_host||'imap.gmail.com')}" style="width:180px"></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Daily Limit</label><input type="number" id="oa-edit-daily-limit" value="${a.daily_limit||150}" style="width:90px"></div>
        <div class="form-group oa-edit-smtp-fields" style="${isBrevo?'display:none':''}"><label>New Password <span style="font-weight:400;font-size:10px;color:var(--text-3)">(leave blank to keep)</span></label><input type="password" id="oa-edit-pass" style="width:150px"></div>
      </div>
      <div class="form-row oa-edit-brevo-fields" style="${isBrevo?'':'display:none'}">
        <div class="form-group" style="flex:1"><label>Signature</label><textarea id="oa-edit-signature" rows="2" style="width:100%;padding:6px 8px;border-radius:var(--r);border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:12px;font-family:inherit">${esc(a.signature||'')}</textarea></div>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px">
        <button class="btn btn-primary" onclick="saveOA(${a.id})">Save</button>
        <button class="btn btn-outline" onclick="closeModal()">Cancel</button>
      </div>`);
  }catch(e){alert(e.message)}
}

function toggleOaEditMode(){
  const mode=document.getElementById('oa-edit-mode').value;
  const smtpFields=document.querySelectorAll('.oa-edit-smtp-fields');
  const brevoFields=document.querySelectorAll('.oa-edit-brevo-fields');
  if(mode==='brevo_relay'){
    smtpFields.forEach(el=>el.style.display='none');
    brevoFields.forEach(el=>el.style.display='');
  }else{
    smtpFields.forEach(el=>el.style.display='');
    brevoFields.forEach(el=>el.style.display='none');
  }
}

async function saveOA(id){
  const display_name=document.getElementById('oa-edit-name').value.trim()||null;
  const smtp_host=document.getElementById('oa-edit-smtp-host').value.trim();
  const smtp_port=parseInt(document.getElementById('oa-edit-smtp-port').value);
  const imap_host=document.getElementById('oa-edit-imap-host').value.trim();
  const daily_limit=parseInt(document.getElementById('oa-edit-daily-limit').value);
  const password=document.getElementById('oa-edit-pass')?document.getElementById('oa-edit-pass').value||null:null;
  const sending_mode=document.getElementById('oa-edit-mode').value;
  const signature=document.getElementById('oa-edit-signature')?document.getElementById('oa-edit-signature').value.trim()||null:null;
  const payload={display_name,smtp_host,smtp_port,imap_host,daily_limit,sending_mode,signature};
  if(password)payload.password=password;
  try{
    await API('/api/outreach-accounts/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    closeModal();loadOutreachAccounts();
  }catch(e){alert('Save failed: '+e.message)}
}

// ── Settings ──
async function loadSettings(){
  try{
    const c=await API('/api/config');
    Object.entries(c).forEach(([k,v])=>{
      const el=document.getElementById('cfg-'+k);
      if(!el)return;
      if(el.type==='checkbox')el.checked=v==='true'||v==='1'||v==='yes';
      else el.value=v;
    });
  }catch{}
  loadOutreachAccounts();
}
async function saveSettings(){const u={};document.querySelectorAll('[id^="cfg-"]').forEach(el=>{const val=el.type==='checkbox'?(el.checked?'true':'false'):el.value;u[el.id.replace('cfg-','')]=val});const st=document.getElementById('settings-status');try{await API('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(u)});st.style.display='';st.style.background='rgba(0,230,118,.1)';st.style.border='1px solid rgba(0,230,118,.3)';st.style.color='var(--success)';st.textContent='✓ Saved.';setTimeout(()=>st.style.display='none',4000);testSmtp()}catch(e){st.style.display='';st.style.background='rgba(255,23,68,.1)';st.style.color='var(--danger)';st.textContent='✗ '+e.message}}

// ── Job Stop ──
function stopJob(n){const es=S.jobs[n];if(es){es.close();delete S.jobs[n]}
if(n==='leadgen'){document.getElementById('lg-btn').disabled=false;document.getElementById('lg-stop').style.display='none';appendLog('terminal','warning','Stopped.')}
if(n==='outreach'){document.getElementById('out-stop-btn').style.display='none';document.getElementById('out-send-btn').disabled=false;appendLog('out-terminal','warning','Stopped.')}
if(n==='intel'){document.getElementById('intel-btn').disabled=false;document.getElementById('intel-stop').style.display='none';document.getElementById('intel-progress').classList.remove('visible');appendLog('intel-terminal','warning','Stopped.')}}

// ── Helpers ──
function appendLog(tid,type,message){const t=document.getElementById(tid);if(!t)return;const ts=new Date().toLocaleTimeString('en-GB');const ic={info:'<i class="fas fa-info-circle"></i>',success:'<i class="fas fa-check"></i>',error:'<i class="fas fa-times"></i>',warning:'<i class="fas fa-exclamation-triangle"></i>',sent:'<i class="fas fa-paper-plane"></i>',skip:'<i class="fas fa-minus"></i>',generating:'<i class="fas fa-sync-alt fa-spin"></i>',progress:'<i class="fas fa-spinner fa-spin"></i>',summary:'<i class="fas fa-star"></i>',done:'<i class="fas fa-check"></i>'};const cl={info:'log-info',success:'log-success',error:'log-error',warning:'log-warning',sent:'log-sent',skip:'log-skip',generating:'log-generating',progress:'log-progress',summary:'log-success',done:'log-success'};const l=document.createElement('div');l.innerHTML=`<span class="log-ts">${ts}</span><span class="log-prefix ${cl[type]||'log-info'}">${ic[type]||'<i class="fas fa-circle"></i>'}</span><span class="${cl[type]||'log-info'}">${esc(message)}</span>`;t.appendChild(l);t.scrollTop=t.scrollHeight}
function clearLog(tid){const t=document.getElementById(tid);if(t)t.innerHTML=''}
function setProgress(fid,lid,pid,v,total){const p=total>0?Math.min(100,Math.round(v/total*100)):0;document.getElementById(fid).style.width=p+'%';document.getElementById(lid).textContent=fN(v)+' / '+fN(total)+' leads';document.getElementById(pid).textContent=p+'%'}
const _pagerCbs={};
function renderPager(cid,off,lim,tot,fn){
  _pagerCbs[cid]=fn;
  const c=document.getElementById(cid);
  const pp=Math.ceil(tot/lim),cur=Math.floor(off/lim);
  if(pp<=1){c.innerHTML='';return}
  const n=fn.name||cid;
  let h=`<span>Page ${cur+1}/${pp}</span>`;
  if(cur>0)h+=`<button class="btn-page" onclick="_pagerCbs['${cid}'](${(cur-1)*lim})">‹</button>`;
  for(let i=Math.max(0,cur-2);i<=Math.min(pp-1,cur+2);i++)h+=`<button class="btn-page${i===cur?' active':''}" onclick="_pagerCbs['${cid}'](${i*lim})">${i+1}</button>`;
  if(cur<pp-1)h+=`<button class="btn-page" onclick="_pagerCbs['${cid}'](${(cur+1)*lim})">›</button>`;
  c.innerHTML=h
}
function openModal(h){document.getElementById('modal-content').innerHTML=h;document.getElementById('modal-overlay').classList.add('open')}
function closeModal(e){if(!e||e.target===document.getElementById('modal-overlay'))document.getElementById('modal-overlay').classList.remove('open')}
function $(id,v){const el=document.getElementById(id);if(el)el.textContent=v}
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function fN(n){return n!=null?Number(n).toLocaleString():'0'}
function formatPercent(p){if(p==null)return'—';return Math.round(Number(p)*100)+'%'}
function fD(s){if(!s)return'—';try{return new Date(s).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'2-digit'})}catch{return s}}
function statusBadge(s){return`<span class="badge ${({active:'badge-green',paused:'badge-warn'})[s]||'badge-dim'}">${s||'?'}</span>`}
function tryJ(s,d){try{return JSON.parse(s)}catch{return d}}

(async()=>{
  if (!S.token) { showLoginModal(); return; }
  testSmtp();
  setInterval(testSmtp, 30000);
  loadDash();
})();
