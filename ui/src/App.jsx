import { useState, useEffect, useCallback, useMemo } from "react"
import { AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts"
import { Server, Shield, Activity, FileText, Settings, Terminal, Plus, Search, CheckCircle, XCircle, Clock, Users, Trash2, Edit, RefreshCw, Download, Eye, Key, Zap, AlertTriangle, Globe, X, ToggleLeft, ToggleRight, Lock, ChevronRight, LogOut, Wifi, WifiOff } from "lucide-react"
import * as api from "./api.js"

// ── Palette ──────────────────────────────────────────────────────────────────
const C = {
  bg:'#04070f', surface:'#080d1c', card:'#0c1228', border:'#162040',
  text:'#cdd6f4', muted:'#4a5980', dim:'#1a2540',
  blue:'#89b4fa', green:'#a6e3a1', red:'#f38ba8',
  yellow:'#f9e2af', cyan:'#89dceb', purple:'#cba6f7', peach:'#fab387',
}
const FUI  = "'Outfit', sans-serif"
const FMONO= "'JetBrains Mono', monospace"

// ── Atoms ─────────────────────────────────────────────────────────────────────
const EnvBadge = ({ env }) => {
  const m = { production:[C.red,'PROD'], staging:[C.yellow,'STG'], development:[C.green,'DEV'], all:[C.cyan,'ALL'] }
  const [c,l] = m[env] || [C.muted, String(env||'?').toUpperCase()]
  return <span style={{background:c+'18',color:c,border:`1px solid ${c}44`,padding:'2px 8px',borderRadius:4,fontSize:10,fontFamily:FMONO,fontWeight:700,letterSpacing:1}}>{l}</span>
}
const SevBadge = ({s}) => {
  const m = {critical:C.red,high:C.peach,medium:C.yellow,info:C.green}
  const c = m[s]||C.muted
  return <span style={{background:c+'18',color:c,border:`1px solid ${c}44`,padding:'2px 8px',borderRadius:4,fontSize:10,fontFamily:FMONO,fontWeight:700}}>{(s||'').toUpperCase()}</span>
}
const Dot = ({on}) => <span style={{width:8,height:8,borderRadius:'50%',display:'inline-block',background:on?C.green:C.red,boxShadow:`0 0 8px ${on?C.green:C.red}88`,flexShrink:0}}/>
const Tip = ({active,payload,label}) => {
  if(!active||!payload?.length) return null
  return <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:'8px 14px',fontFamily:FMONO,fontSize:12}}>
    <div style={{color:C.muted,marginBottom:6}}>{label}</div>
    {payload.map((p,i)=><div key={i} style={{color:p.color}}>{p.name}: <b>{p.value}</b></div>)}
  </div>
}
const Btn = ({children,onClick,variant='primary',small,icon:Icon,disabled,loading}) => {
  const bg = variant==='primary'?C.blue:variant==='danger'?C.red:'transparent'
  const fg = variant==='ghost'?C.text:C.bg
  return <button onClick={onClick} disabled={disabled||loading} style={{
    display:'flex',alignItems:'center',gap:6,background:bg+(variant==='ghost'?'':loading?'99':'ee'),
    color:fg,border:`1px solid ${variant==='ghost'?C.border:'transparent'}`,borderRadius:8,
    padding:small?'6px 12px':'9px 18px',fontFamily:FUI,fontWeight:600,fontSize:small?12:13,
    cursor:disabled||loading?'not-allowed':'pointer',whiteSpace:'nowrap',opacity:disabled?0.5:1,
  }}>{loading?<RefreshCw size={14} style={{animation:'spin 1s linear infinite'}}/>:Icon&&<Icon size={14}/>}{children}</button>
}
const Inp = ({value,onChange,placeholder,type='text',style}) => (
  <input type={type} value={value} onChange={e=>onChange(e.target.value)} placeholder={placeholder} style={{
    background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:'9px 14px',
    color:C.text,fontFamily:FMONO,fontSize:13,outline:'none',width:'100%',boxSizing:'border-box',...style,
  }}/>
)
const Sel = ({value,onChange,options,style}) => (
  <select value={value} onChange={e=>onChange(e.target.value)} style={{
    background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:'9px 14px',
    color:C.text,fontFamily:FMONO,fontSize:13,outline:'none',cursor:'pointer',width:'100%',boxSizing:'border-box',...style,
  }}>{options.map(o=><option key={o.value??o} value={o.value??o}>{o.label??o}</option>)}</select>
)
const Modal = ({open,onClose,title,children,width=500}) => {
  if(!open) return null
  return <div style={{position:'fixed',inset:0,background:'#00000099',zIndex:1000,display:'flex',alignItems:'center',justifyContent:'center'}} onClick={e=>{if(e.target===e.currentTarget)onClose()}}>
    <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:16,width,maxHeight:'85vh',overflow:'auto',boxShadow:'0 32px 80px #000c'}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'20px 24px',borderBottom:`1px solid ${C.border}`}}>
        <span style={{fontFamily:FUI,fontWeight:800,fontSize:16,color:C.text}}>{title}</span>
        <button onClick={onClose} style={{background:'none',border:'none',color:C.muted,cursor:'pointer'}}><X size={18}/></button>
      </div>
      <div style={{padding:24}}>{children}</div>
    </div>
  </div>
}
const Alert = ({msg,type='error'}) => {
  if(!msg) return null
  const c = type==='error'?C.red:type==='success'?C.green:C.yellow
  return <div style={{background:c+'18',border:`1px solid ${c}44`,borderRadius:8,padding:'10px 14px',display:'flex',gap:10,alignItems:'center'}}>
    {type==='error'?<AlertTriangle size={14} color={c}/>:<CheckCircle size={14} color={c}/>}
    <span style={{fontFamily:FUI,fontSize:13,color:c}}>{msg}</span>
  </div>
}
const Spinner = () => <div style={{display:'flex',justifyContent:'center',padding:40,color:C.muted,fontFamily:FUI}}>Chargement…</div>
const KpiCard = ({icon:Icon,label,value,sub,color,loading}) => (
  <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:'20px 24px',position:'relative',overflow:'hidden'}}>
    <div style={{position:'absolute',top:0,left:0,right:0,height:2,background:`linear-gradient(90deg,${color},transparent)`}}/>
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:12}}>
      <span style={{color:C.muted,fontSize:11,textTransform:'uppercase',letterSpacing:1.5,fontFamily:FUI,fontWeight:600}}>{label}</span>
      <div style={{background:color+'20',border:`1px solid ${color}44`,borderRadius:8,padding:'6px 8px'}}><Icon size={14} color={color}/></div>
    </div>
    <div style={{fontSize:34,fontWeight:800,color:loading?C.muted:C.text,fontFamily:FMONO,lineHeight:1}}>{loading?'…':value}</div>
    <div style={{fontSize:11,color:C.muted,marginTop:8}}>{sub}</div>
  </div>
)

// ── useApi hook ───────────────────────────────────────────────────────────────
function useApi(fn, deps=[]) {
  const [data,  setData]  = useState(null)
  const [error, setError] = useState(null)
  const [loading,setLoad] = useState(true)
  const load = useCallback(async () => {
    setLoad(true); setError(null)
    try { setData(await fn()) }
    catch(e) { setError(e.message) }
    finally { setLoad(false) }
  }, deps) // eslint-disable-line
  useEffect(() => { load() }, [load])
  return { data, error, loading, reload: load }
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
function Dashboard() {
  const {data, loading, reload} = useApi(api.metrics)
  const envDist = useMemo(() => {
    if(!data) return []
    const t = Object.values(data.env_breakdown||{}).reduce((a,b)=>a+b,0)||1
    const colors = {production:C.red,staging:C.yellow,development:C.green}
    return Object.entries(data.env_breakdown||{}).map(([k,v])=>({name:k,value:Math.round(v/t*100),color:colors[k]||C.muted}))
  }, [data])

  return (
    <div style={{display:'flex',flexDirection:'column',gap:24}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
        <h2 style={{fontFamily:FUI,fontWeight:800,fontSize:22,color:C.text,margin:0}}>Dashboard</h2>
        <Btn icon={RefreshCw} variant="ghost" small onClick={reload}>Rafraîchir</Btn>
      </div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:16}}>
        <KpiCard icon={Zap}    label="Commandes 24h"  value={data?.total_24h??'—'}   sub="total toutes envs"       color={C.blue}  loading={loading}/>
        <KpiCard icon={CheckCircle} label="Autorisées 24h" value={data?.allowed_24h??'—'} sub="exécutées avec succès"  color={C.green} loading={loading}/>
        <KpiCard icon={Shield} label="Bloquées 24h"   value={data?.denied_24h??'—'}  sub="par OPA policies"         color={C.red}   loading={loading}/>
        <KpiCard icon={Server} label="Hôtes actifs"   value={data?.total_hosts??'—'} sub="dans le registre"         color={C.cyan}  loading={loading}/>
      </div>
      <div style={{display:'grid',gridTemplateColumns:'2fr 1fr',gap:16}}>
        <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:24}}>
          <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:20}}>
            <h3 style={{fontFamily:FUI,fontWeight:800,fontSize:16,color:C.text,margin:0}}>Activité — 24h</h3>
            <div style={{display:'flex',gap:16,fontSize:11,fontFamily:FMONO}}>
              <span style={{color:C.blue}}>● commandes</span><span style={{color:C.red}}>● refusées</span>
            </div>
          </div>
          {loading?<Spinner/>:
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={data?.hourly||[]}>
              <defs>
                <linearGradient id="gCmd" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.blue} stopOpacity={0.3}/><stop offset="95%" stopColor={C.blue} stopOpacity={0}/></linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={C.dim}/>
              <XAxis dataKey="t" stroke={C.muted} fontSize={10} tick={{fontFamily:FMONO}} interval={5}/>
              <YAxis stroke={C.muted} fontSize={10} tick={{fontFamily:FMONO}}/>
              <Tooltip content={<Tip/>}/>
              <Area type="monotone" dataKey="commands" name="commandes" stroke={C.blue} fill="url(#gCmd)" strokeWidth={2}/>
              <Area type="monotone" dataKey="denied" name="refusées" stroke={C.red} fill="none" strokeWidth={2} strokeDasharray="4 2"/>
            </AreaChart>
          </ResponsiveContainer>}
        </div>
        <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:24}}>
          <h3 style={{fontFamily:FUI,fontWeight:800,fontSize:16,color:C.text,margin:'0 0 16px'}}>Répartition env</h3>
          {loading?<Spinner/>:<>
          <ResponsiveContainer width="100%" height={160}>
            <PieChart><Pie data={envDist} cx="50%" cy="50%" innerRadius={50} outerRadius={75} paddingAngle={4} dataKey="value">
              {envDist.map((e,i)=><Cell key={i} fill={e.color} opacity={0.85}/>)}
            </Pie><Tooltip content={({active,payload})=>active&&payload?.length?<div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:'8px 14px',fontFamily:FMONO,fontSize:12,color:C.text}}>{payload[0].name}: <b>{payload[0].value}%</b></div>:null}/></PieChart>
          </ResponsiveContainer>
          <div style={{display:'flex',flexDirection:'column',gap:8}}>
            {envDist.map((e,i)=><div key={i} style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <div style={{display:'flex',alignItems:'center',gap:8}}><span style={{width:10,height:10,borderRadius:2,background:e.color,display:'inline-block'}}/><span style={{fontSize:12,color:C.muted,fontFamily:FUI}}>{e.name}</span></div>
              <span style={{fontSize:13,color:C.text,fontFamily:FMONO,fontWeight:700}}>{e.value}%</span>
            </div>)}
          </div>
          </>}
        </div>
      </div>
    </div>
  )
}

// ── Hosts ─────────────────────────────────────────────────────────────────────
const EMPTY_HOST = {name:'',hostname:'',port:22,ssh_user:'deploy',env:'development',vault_role:'dev-ssh',allowed_roles:'dev',enabled:true}

function HostsView() {
  const {data:hostList, loading, error, reload} = useApi(api.hosts.list)
  const [search,  setSearch]  = useState('')
  const [envF,    setEnvF]    = useState('all')
  const [showAdd, setShowAdd] = useState(false)
  const [editId,  setEditId]  = useState(null)
  const [form,    setForm]    = useState(EMPTY_HOST)
  const [saving,  setSaving]  = useState(false)
  const [formErr, setFormErr] = useState(null)

  const filtered = useMemo(() => (hostList||[]).filter(h =>
    (envF==='all'||h.env===envF) && (h.name.includes(search)||h.hostname.includes(search))
  ), [hostList,search,envF])

  const openAdd  = () => { setForm(EMPTY_HOST); setEditId(null); setFormErr(null); setShowAdd(true) }
  const openEdit = h => { setForm({...h, allowed_roles: Array.isArray(h.allowed_roles)?h.allowed_roles.join(','):h.allowed_roles}); setEditId(h.id); setFormErr(null); setShowAdd(true) }
  const save = async () => {
    setSaving(true); setFormErr(null)
    try {
      if(editId) await api.hosts.update(editId, form)
      else       await api.hosts.create(form)
      setShowAdd(false); reload()
    } catch(e) { setFormErr(e.message) }
    finally { setSaving(false) }
  }
  const del = async id => {
    if(!confirm('Supprimer cet hôte ?')) return
    try { await api.hosts.delete(id); reload() }
    catch(e) { alert(e.message) }
  }

  return (
    <div style={{display:'flex',flexDirection:'column',gap:20}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
        <div>
          <h2 style={{fontFamily:FUI,fontWeight:800,fontSize:22,color:C.text,margin:0}}>Host Registry</h2>
          <p style={{color:C.muted,fontSize:13,marginTop:4,fontFamily:FUI}}>{(hostList||[]).length} hôtes enregistrés</p>
        </div>
        <div style={{display:'flex',gap:10}}>
          <Btn icon={RefreshCw} variant="ghost" small onClick={reload}>Sync</Btn>
          <Btn icon={Plus} onClick={openAdd}>Ajouter</Btn>
        </div>
      </div>
      {error&&<Alert msg={error}/>}
      <div style={{display:'flex',gap:12}}>
        <div style={{position:'relative',flex:1}}>
          <Search size={14} color={C.muted} style={{position:'absolute',left:12,top:'50%',transform:'translateY(-50%)'}}/>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Rechercher nom, IP…" style={{width:'100%',background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:'9px 14px 9px 36px',color:C.text,fontFamily:FMONO,fontSize:13,outline:'none',boxSizing:'border-box'}}/>
        </div>
        <Sel value={envF} onChange={setEnvF} options={[{value:'all',label:'Tous envs'},{value:'production',label:'Production'},{value:'staging',label:'Staging'},{value:'development',label:'Development'}]} style={{width:170}}/>
      </div>
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,overflow:'hidden'}}>
        {loading?<Spinner/>:
        <table style={{width:'100%',borderCollapse:'collapse'}}>
          <thead><tr style={{background:C.surface}}>
            {['','Nom','IP / Hostname','Env','User SSH','Rôles','Vault Role',''].map(h=>(
              <th key={h} style={{padding:'12px 16px',textAlign:'left',fontSize:11,color:C.muted,fontFamily:FUI,fontWeight:600,textTransform:'uppercase',letterSpacing:1,borderBottom:`1px solid ${C.border}`}}>{h}</th>
            ))}
          </tr></thead>
          <tbody>{filtered.map((h,i)=>(
            <tr key={h.id} style={{borderBottom:i<filtered.length-1?`1px solid ${C.dim}`:'none',transition:'background 0.15s'}}
              onMouseEnter={e=>e.currentTarget.style.background=C.surface}
              onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
              <td style={{padding:'14px 16px'}}><Dot on={h.enabled}/></td>
              <td style={{padding:'14px 16px',fontFamily:FMONO,fontSize:13,color:C.blue,fontWeight:700}}>{h.name}</td>
              <td style={{padding:'14px 16px',fontFamily:FMONO,fontSize:12,color:C.text}}>{h.hostname}:{h.port}</td>
              <td style={{padding:'14px 16px'}}><EnvBadge env={h.env}/></td>
              <td style={{padding:'14px 16px',fontFamily:FMONO,fontSize:12,color:C.text}}>{h.ssh_user}</td>
              <td style={{padding:'14px 16px'}}><div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
                {(Array.isArray(h.allowed_roles)?h.allowed_roles:h.allowed_roles.split(',')).map(r=>(
                  <span key={r} style={{background:C.purple+'20',color:C.purple,border:`1px solid ${C.purple}44`,padding:'1px 6px',borderRadius:4,fontSize:10,fontFamily:FMONO}}>{r.trim()}</span>
                ))}
              </div></td>
              <td style={{padding:'14px 16px',fontFamily:FMONO,fontSize:11,color:C.muted}}>{h.vault_role}</td>
              <td style={{padding:'14px 16px'}}><div style={{display:'flex',gap:8}}>
                <button onClick={()=>openEdit(h)} style={{background:'none',border:'none',color:C.muted,cursor:'pointer',padding:4}}><Edit size={14}/></button>
                <button onClick={()=>del(h.id)} style={{background:'none',border:'none',color:C.red+'aa',cursor:'pointer',padding:4}}><Trash2 size={14}/></button>
              </div></td>
            </tr>
          ))}</tbody>
        </table>}
        {!loading&&filtered.length===0&&<div style={{padding:40,textAlign:'center',color:C.muted,fontFamily:FUI}}>Aucun hôte{search?' correspondant':''}</div>}
      </div>

      <Modal open={showAdd} onClose={()=>setShowAdd(false)} title={editId?'Modifier l\'hôte':'Nouvel hôte'} width={540}>
        <div style={{display:'flex',flexDirection:'column',gap:16}}>
          {formErr&&<Alert msg={formErr}/>}
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14}}>
            {[['Nom','name','prod-web-01'],['Hostname / IP','hostname','10.0.1.10'],['Port','port','22'],['User SSH','ssh_user','deploy'],['Vault Role','vault_role','prod-ssh'],['Rôles (virgule)','allowed_roles','ops, sre']].map(([label,key,ph])=>(
              <div key={key} style={{display:'flex',flexDirection:'column',gap:6}}>
                <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>{label}</label>
                <Inp value={String(form[key]||'')} onChange={v=>setForm(f=>({...f,[key]:v}))} placeholder={ph}/>
              </div>
            ))}
          </div>
          <div style={{display:'flex',flexDirection:'column',gap:6}}>
            <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Environnement</label>
            <Sel value={form.env} onChange={v=>setForm(f=>({...f,env:v}))} options={[{value:'production',label:'Production'},{value:'staging',label:'Staging'},{value:'development',label:'Development'}]}/>
          </div>
          <div style={{display:'flex',justifyContent:'flex-end',gap:12,marginTop:8}}>
            <Btn variant="ghost" onClick={()=>setShowAdd(false)}>Annuler</Btn>
            <Btn onClick={save} loading={saving}>Enregistrer</Btn>
          </div>
        </div>
      </Modal>
    </div>
  )
}

// ── Policies ──────────────────────────────────────────────────────────────────
function PoliciesView() {
  const {data, loading, error, reload} = useApi(api.policies.list)
  const [editP,    setEditP]   = useState(null)
  const [showEdit, setShowEdit]= useState(false)
  const [pForm,    setPForm]   = useState({})
  const [saving,   setSaving]  = useState(false)
  const [pErr,     setPErr]    = useState(null)

  const openEdit = p => { setPForm({...p}); setEditP(p.id); setPErr(null); setShowEdit(true) }
  const openNew  = () => { setPForm({name:'',env:'all',pattern:'',action:'deny',severity:'high',enabled:true}); setEditP(null); setPErr(null); setShowEdit(true) }

  const saveP = async () => {
    setSaving(true); setPErr(null)
    try {
      if(editP) await api.policies.update(editP, pForm)
      else      await api.policies.create(pForm)
      setShowEdit(false); reload()
    } catch(e) { setPErr(e.message) }
    finally { setSaving(false) }
  }

  const toggle = async p => {
    try { await api.policies.update(p.id, {...p, enabled:!p.enabled}); reload() }
    catch(e) { alert(e.message) }
  }
  const del = async id => {
    if(!confirm('Supprimer cette règle ?')) return
    try { await api.policies.delete(id); reload() }
    catch(e) { alert(e.message) }
  }

  const colMap = {deny:C.red, allow:C.green, warn:C.yellow}

  return (
    <div style={{display:'flex',flexDirection:'column',gap:20}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
        <div>
          <h2 style={{fontFamily:FUI,fontWeight:800,fontSize:22,color:C.text,margin:0}}>OPA Policies</h2>
          <p style={{color:C.muted,fontSize:13,marginTop:4,fontFamily:FUI}}>{(data||[]).filter(p=>p.enabled).length} règles actives</p>
        </div>
        <div style={{display:'flex',gap:10}}>
          <Btn icon={RefreshCw} variant="ghost" small onClick={reload}/>
          <Btn icon={Plus} onClick={openNew}>Nouvelle règle</Btn>
        </div>
      </div>
      {error&&<Alert msg={error}/>}
      {loading?<Spinner/>:
      <div style={{display:'flex',flexDirection:'column',gap:10}}>
        {(data||[]).map(p=>(
          <div key={p.id} style={{background:C.card,border:`1px solid ${p.enabled?C.border:C.dim}`,borderRadius:14,padding:'18px 24px',display:'flex',alignItems:'center',gap:16,opacity:p.enabled?1:0.5,transition:'all 0.2s'}}>
            <div style={{width:4,height:40,borderRadius:4,background:p.enabled?colMap[p.action]:C.dim,flexShrink:0}}/>
            <div style={{flex:1}}>
              <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:6,flexWrap:'wrap'}}>
                <span style={{fontFamily:FUI,fontWeight:700,fontSize:14,color:C.text}}>{p.name}</span>
                <SevBadge s={p.severity}/><EnvBadge env={p.env}/>
              </div>
              <div style={{fontFamily:FMONO,fontSize:12,color:C.muted}}>
                <span style={{color:colMap[p.action],fontWeight:700}}>{(p.action||'').toUpperCase()}</span>
                {' '}pattern: <span style={{color:C.cyan}}>{p.pattern}</span>
              </div>
            </div>
            <div style={{textAlign:'right',flexShrink:0}}>
              <div style={{fontFamily:FMONO,fontSize:22,fontWeight:800,color:(p.triggers||0)>10?C.red:C.muted}}>{p.triggers||0}</div>
              <div style={{fontSize:10,color:C.muted,textTransform:'uppercase',letterSpacing:1}}>triggers</div>
            </div>
            <div style={{display:'flex',gap:8,flexShrink:0}}>
              <button onClick={()=>openEdit(p)} style={{background:'none',border:'none',color:C.muted,cursor:'pointer',padding:6}}><Edit size={15}/></button>
              <button onClick={()=>del(p.id)} style={{background:'none',border:'none',color:C.red+'aa',cursor:'pointer',padding:6}}><Trash2 size={15}/></button>
              <button onClick={()=>toggle(p)} style={{background:'none',border:'none',color:p.enabled?C.green:C.muted,cursor:'pointer',padding:6}}>
                {p.enabled?<ToggleRight size={22}/>:<ToggleLeft size={22}/>}
              </button>
            </div>
          </div>
        ))}
      </div>}

      <Modal open={showEdit} onClose={()=>setShowEdit(false)} title="Éditeur de règle" width={560}>
        <div style={{display:'flex',flexDirection:'column',gap:14}}>
          {pErr&&<Alert msg={pErr}/>}
          <div style={{display:'flex',flexDirection:'column',gap:6}}>
            <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Nom</label>
            <Inp value={pForm.name||''} onChange={v=>setPForm(f=>({...f,name:v}))} placeholder="Block rm -rf"/>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:12}}>
            {[['Env','env',[{value:'all',label:'Tous'},{value:'production',label:'Production'},{value:'staging',label:'Staging'},{value:'development',label:'Dev'}]],
              ['Action','action',['deny','allow','warn']],
              ['Sévérité','severity',['critical','high','medium','info']]].map(([label,key,opts])=>(
              <div key={key} style={{display:'flex',flexDirection:'column',gap:6}}>
                <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>{label}</label>
                <Sel value={pForm[key]||''} onChange={v=>setPForm(f=>({...f,[key]:v}))} options={opts}/>
              </div>
            ))}
          </div>
          <div style={{display:'flex',flexDirection:'column',gap:6}}>
            <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Pattern regex</label>
            <Inp value={pForm.pattern||''} onChange={v=>setPForm(f=>({...f,pattern:v}))} placeholder="rm\\s+-rf"/>
          </div>
          <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:14,fontFamily:FMONO,fontSize:11,color:C.cyan,lineHeight:1.7}}>
            <div style={{color:C.muted,marginBottom:6,fontFamily:FUI,fontSize:11,textTransform:'uppercase',letterSpacing:1}}>Aperçu Rego</div>
            {`deny[msg] {\n  input.env == "${pForm.env||'all'}"\n  re_match(\`${pForm.pattern||'.*'}\`, input.command)\n  msg := "Rule: ${pForm.name||'unnamed'}"\n}`}
          </div>
          <div style={{display:'flex',justifyContent:'flex-end',gap:12}}>
            <Btn variant="ghost" onClick={()=>setShowEdit(false)}>Annuler</Btn>
            <Btn onClick={saveP} loading={saving}>Enregistrer</Btn>
          </div>
        </div>
      </Modal>
    </div>
  )
}

// ── Audit ─────────────────────────────────────────────────────────────────────
function AuditView() {
  const [statusF, setStatusF] = useState('')
  const [envF,    setEnvF]    = useState('')
  const [search,  setSearch]  = useState('')
  const [exp,     setExp]     = useState(null)

  const fetcher = useCallback(() => api.audit({limit:100, env:envF||undefined, status:statusF||undefined}), [envF, statusF])
  const {data, loading, error, reload} = useApi(fetcher, [envF, statusF])

  const filtered = useMemo(() => (data||[]).filter(r =>
    !search || r.command.includes(search) || r.user.includes(search) || r.host_name.includes(search)
  ), [data, search])

  return (
    <div style={{display:'flex',flexDirection:'column',gap:20}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
        <div>
          <h2 style={{fontFamily:FUI,fontWeight:800,fontSize:22,color:C.text,margin:0}}>Audit Log</h2>
          <p style={{color:C.muted,fontSize:13,marginTop:4,fontFamily:FUI}}>Immutable · SHA-256 verified</p>
        </div>
        <div style={{display:'flex',gap:10}}>
          <Btn icon={RefreshCw} variant="ghost" small onClick={reload}/>
          <Btn icon={Download} variant="ghost" small onClick={()=>{
            const csv = ['id,ts,user,host,env,command,status,duration_ms,deny_reason',
              ...(filtered||[]).map(r=>[r.id,r.timestamp,r.user,r.host_name,r.host_env,`"${r.command}"`,r.status,r.duration_ms||'',r.deny_reason||''].join(','))
            ].join('\n')
            const a = document.createElement('a'); a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(csv); a.download='audit.csv'; a.click()
          }}>Export CSV</Btn>
        </div>
      </div>
      {error&&<Alert msg={error}/>}
      <div style={{display:'flex',gap:12}}>
        <div style={{position:'relative',flex:1}}>
          <Search size={14} color={C.muted} style={{position:'absolute',left:12,top:'50%',transform:'translateY(-50%)'}}/>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Rechercher commande, user, hôte…" style={{width:'100%',background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:'9px 14px 9px 36px',color:C.text,fontFamily:FMONO,fontSize:13,outline:'none',boxSizing:'border-box'}}/>
        </div>
        <Sel value={statusF} onChange={setStatusF} options={[{value:'',label:'Tous statuts'},{value:'allowed',label:'Autorisées'},{value:'denied',label:'Refusées'}]} style={{width:160}}/>
        <Sel value={envF}    onChange={setEnvF}    options={[{value:'',label:'Tous envs'},{value:'production',label:'Production'},{value:'staging',label:'Staging'},{value:'development',label:'Dev'}]} style={{width:160}}/>
      </div>
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,overflow:'hidden'}}>
        {loading?<Spinner/>:filtered.map((r,i)=>(
          <div key={r.id}>
            <div onClick={()=>setExp(exp===r.id?null:r.id)} style={{display:'flex',alignItems:'center',gap:14,padding:'13px 20px',cursor:'pointer',borderBottom:`1px solid ${C.dim}`,transition:'background 0.15s',background:exp===r.id?C.surface:'transparent'}}
              onMouseEnter={e=>{if(exp!==r.id)e.currentTarget.style.background=C.surface+'88'}}
              onMouseLeave={e=>{if(exp!==r.id)e.currentTarget.style.background='transparent'}}>
              {r.status==='allowed'?<CheckCircle size={14} color={C.green} style={{flexShrink:0}}/>:<XCircle size={14} color={C.red} style={{flexShrink:0}}/>}
              <span style={{fontFamily:FMONO,fontSize:10,color:C.muted,width:80,flexShrink:0}}>{r.timestamp?.slice(11,19)}</span>
              <span style={{fontFamily:FMONO,fontSize:12,color:C.blue,width:90,flexShrink:0}}>{r.user}</span>
              <span style={{fontFamily:FMONO,fontSize:12,color:C.cyan,width:130,flexShrink:0}}>{r.host_name}</span>
              <span style={{fontFamily:FMONO,fontSize:13,color:C.text,flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{r.command}</span>
              <EnvBadge env={r.host_env}/>
              {r.duration_ms>0&&<span style={{fontFamily:FMONO,fontSize:11,color:C.muted,width:60,textAlign:'right',flexShrink:0}}>{r.duration_ms}ms</span>}
              <ChevronRight size={14} color={C.muted} style={{flexShrink:0,transform:exp===r.id?'rotate(90deg)':'none',transition:'transform 0.2s'}}/>
            </div>
            {exp===r.id&&<div style={{background:C.surface,padding:'16px 24px',borderBottom:`1px solid ${C.dim}`}}>
              <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:16,marginBottom:r.deny_reason?12:0}}>
                {[['Session ID',r.id?.slice(0,16)+'…'],['Exit code',r.exit_code??'N/A'],['Durée',r.duration_ms?`${r.duration_ms}ms`:'-'],['Utilisateur',r.user],['Hôte',r.host_name],['Hash stdout',r.stdout_hash?r.stdout_hash.slice(0,20)+'…':'-']].map(([k,v])=>(
                  <div key={k}><div style={{fontSize:10,color:C.muted,textTransform:'uppercase',letterSpacing:1,marginBottom:4}}>{k}</div><div style={{fontFamily:FMONO,fontSize:12,color:C.text}}>{v}</div></div>
                ))}
              </div>
              {r.deny_reason&&<div style={{background:C.red+'15',border:`1px solid ${C.red}44`,borderRadius:8,padding:'10px 14px',display:'flex',gap:10,alignItems:'center'}}>
                <AlertTriangle size={14} color={C.red}/><span style={{fontFamily:FMONO,fontSize:12,color:C.red}}>{r.deny_reason}</span>
              </div>}
            </div>}
          </div>
        ))}
        {!loading&&filtered.length===0&&<div style={{padding:40,textAlign:'center',color:C.muted,fontFamily:FUI}}>Aucune entrée</div>}
      </div>
    </div>
  )
}

// ── Settings ──────────────────────────────────────────────────────────────────
function SettingsView() {
  const {data:health, loading, reload} = useApi(api.health)
  const [tab,   setTab]   = useState('gateway')
  const [saved, setSaved] = useState(false)
  const [env,   setEnv]   = useState({JWT_SECRET:'',JWT_EXPIRE_HOURS:'24',RATE_PROD:'10',RATE_STG:'30',RATE_DEV:'100',SSH_MODE:'key',SSH_KEY_PATH:'~/.ssh/id_rsa',VAULT_URL:'',VAULT_TOKEN:'',VAULT_MOUNT:'ssh'})

  // Vault KV form state
  const [vaultKey,        setVaultKey]        = useState('')
  const [vaultPass,       setVaultPass]       = useState('')
  const [vaultStatus,     setVaultStatus]     = useState(null)  // {stored, has_passphrase}
  const [vaultSaving,     setVaultSaving]     = useState(false)
  const [vaultMsg,        setVaultMsg]        = useState(null)  // {type, text}
  const [showKey,         setShowKey]         = useState(false)

  useEffect(() => {
    if(tab === 'ssh') {
      api.vault.status().then(s => setVaultStatus(s)).catch(() => setVaultStatus(null))
    }
  }, [tab])

  const saveVaultKey = async () => {
    if(!vaultKey.trim()) { setVaultMsg({type:'error',text:'La clé privée est requise'}); return }
    setVaultSaving(true); setVaultMsg(null)
    try {
      await api.vault.store(vaultKey.trim(), vaultPass || null)
      setVaultMsg({type:'success',text:'Clé stockée dans Vault avec succès'})
      setVaultKey(''); setVaultPass('')
      const s = await api.vault.status()
      setVaultStatus(s)
    } catch(e) { setVaultMsg({type:'error',text:e.message}) }
    finally { setVaultSaving(false) }
  }

  const deleteVaultKey = async () => {
    if(!confirm('Supprimer la clé privée du Vault ?')) return
    try {
      await api.vault.delete()
      setVaultStatus({stored:false, has_passphrase:false})
      setVaultMsg({type:'success',text:'Clé supprimée du Vault'})
    } catch(e) { setVaultMsg({type:'error',text:e.message}) }
  }

  const F = ({label,k,type='text'}) => (
    <div style={{display:'flex',flexDirection:'column',gap:6}}>
      <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>{label}</label>
      <Inp value={env[k]||''} onChange={v=>setEnv(e=>({...e,[k]:v}))} type={type}/>
    </div>
  )

  const tabs = [{id:'gateway',icon:Globe,label:'Gateway'},{id:'ssh',icon:Key,label:'SSH / Vault'},{id:'status',icon:Activity,label:'État système'}]

  return (
    <div style={{display:'flex',flexDirection:'column',gap:24}}>
      <h2 style={{fontFamily:FUI,fontWeight:800,fontSize:22,color:C.text,margin:0}}>Configuration</h2>
      <div style={{display:'flex',gap:4,background:C.surface,padding:4,borderRadius:10,width:'fit-content',border:`1px solid ${C.border}`}}>
        {tabs.map(t=>(
          <button key={t.id} onClick={()=>setTab(t.id)} style={{display:'flex',alignItems:'center',gap:8,padding:'8px 20px',borderRadius:8,border:'none',background:tab===t.id?C.blue+'ee':'transparent',color:tab===t.id?C.bg:C.muted,cursor:'pointer',fontFamily:FUI,fontWeight:600,fontSize:13,transition:'all 0.15s'}}>
            <t.icon size={14}/>{t.label}
          </button>
        ))}
      </div>

      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,padding:28}}>
        {tab==='gateway'&&<div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
          <F label="JWT Secret (32+ chars)" k="JWT_SECRET" type="password"/>
          <F label="Expiration JWT (heures)" k="JWT_EXPIRE_HOURS"/>
          <F label="Rate limit PROD (cmd/min)" k="RATE_PROD"/>
          <F label="Rate limit STAGING (cmd/min)" k="RATE_STG"/>
          <F label="Rate limit DEV (cmd/min)" k="RATE_DEV"/>
        </div>}

        {tab==='ssh'&&<div style={{display:'flex',flexDirection:'column',gap:20}}>
          <div style={{display:'flex',flexDirection:'column',gap:6}}>
            <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Mode SSH</label>
            <Sel value={env.SSH_MODE} onChange={v=>setEnv(e=>({...e,SSH_MODE:v}))} options={[
              {value:'key',      label:'Clé statique (dev)'},
              {value:'vault-kv', label:'Vault KV — clé uploadée'},
              {value:'vault',    label:'Vault SSH CA — certificats éphémères (prod)'},
            ]}/>
          </div>

          {env.SSH_MODE==='key'&&<F label="Chemin clé privée" k="SSH_KEY_PATH"/>}

          {env.SSH_MODE==='vault'&&<div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
            <F label="Vault URL" k="VAULT_URL"/>
            <F label="Vault Mount" k="VAULT_MOUNT"/>
            <F label="Vault Token" k="VAULT_TOKEN" type="password"/>
          </div>}

          {env.SSH_MODE==='vault-kv'&&<div style={{display:'flex',flexDirection:'column',gap:16}}>
            {/* Statut clé stockée */}
            <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:'14px 18px',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <div style={{display:'flex',alignItems:'center',gap:10}}>
                {vaultStatus?.stored
                  ? <CheckCircle size={16} color={C.green}/>
                  : <XCircle size={16} color={C.muted}/>}
                <div>
                  <div style={{fontFamily:FUI,fontWeight:600,fontSize:13,color:vaultStatus?.stored?C.green:C.muted}}>
                    {vaultStatus?.stored ? 'Clé privée stockée dans Vault' : 'Aucune clé stockée'}
                  </div>
                  {vaultStatus?.stored&&<div style={{fontFamily:FMONO,fontSize:11,color:C.muted,marginTop:2}}>
                    Passphrase : {vaultStatus.has_passphrase ? '●●●●●●' : 'aucune'}
                  </div>}
                </div>
              </div>
              {vaultStatus?.stored&&<Btn icon={Trash2} variant="danger" small onClick={deleteVaultKey}>Supprimer</Btn>}
            </div>

            {vaultMsg&&<Alert msg={vaultMsg.text} type={vaultMsg.type}/>}

            {/* Formulaire upload clé */}
            <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:20,display:'flex',flexDirection:'column',gap:14}}>
              <div style={{fontFamily:FUI,fontWeight:700,fontSize:14,color:C.text,display:'flex',alignItems:'center',gap:8}}>
                <Key size={14} color={C.blue}/>{vaultStatus?.stored ? 'Remplacer la clé' : 'Stocker une clé privée'}
              </div>

              <div style={{display:'flex',flexDirection:'column',gap:6}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                  <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Clé privée SSH (PEM)</label>
                  <button onClick={()=>setShowKey(v=>!v)} style={{background:'none',border:'none',color:C.muted,cursor:'pointer',display:'flex',alignItems:'center',gap:4,fontSize:11,fontFamily:FUI}}>
                    <Eye size={12}/>{showKey?'Masquer':'Afficher'}
                  </button>
                </div>
                <textarea
                  value={vaultKey}
                  onChange={e=>setVaultKey(e.target.value)}
                  placeholder={"-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----"}
                  rows={7}
                  style={{
                    background:C.card,border:`1px solid ${C.border}`,borderRadius:8,
                    padding:'10px 14px',color:showKey?C.text:'transparent',
                    caretColor:C.text,
                    fontFamily:FMONO,fontSize:12,outline:'none',
                    width:'100%',boxSizing:'border-box',resize:'vertical',
                    textShadow:showKey?'none':'0 0 8px rgba(205,214,244,0.8)',
                  }}
                />
              </div>

              <div style={{display:'flex',flexDirection:'column',gap:6}}>
                <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Passphrase (optionnel)</label>
                <Inp value={vaultPass} onChange={setVaultPass} type="password" placeholder="Laisser vide si la clé n'est pas chiffrée"/>
              </div>

              <div style={{display:'flex',justifyContent:'flex-end'}}>
                <Btn icon={Lock} onClick={saveVaultKey} loading={vaultSaving}>Stocker dans Vault</Btn>
              </div>
            </div>
          </div>}

          <div style={{background:C.blue+'12',border:`1px solid ${C.blue}44`,borderRadius:8,padding:'12px 16px',fontFamily:FUI,fontSize:13,color:C.blue}}>
            {env.SSH_MODE==='key'&&'Mode dev : clé SSH statique montée depuis le système de fichiers.'}
            {env.SSH_MODE==='vault-kv'&&'Mode Vault KV : la clé privée est stockée chiffrée dans Vault et récupérée à chaque connexion SSH.'}
            {env.SSH_MODE==='vault'&&'Mode production : Vault génère des certificats ED25519 éphémères (TTL 5min). Aucune clé stockée.'}
          </div>
        </div>}

        {tab==='status'&&<div style={{display:'flex',flexDirection:'column',gap:16}}>
          <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
            <span style={{fontFamily:FUI,fontWeight:600,fontSize:14,color:C.text}}>État du gateway</span>
            <Btn icon={RefreshCw} variant="ghost" small onClick={reload}/>
          </div>
          {loading?<Spinner/>:health&&<>
            <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:12}}>
              {[['Version',health.version||'?',C.blue],['SSH Mode',health.ssh_mode||'?',C.cyan],['Status',health.status||'?',health.status==='ok'?C.green:C.red]].map(([label,val,color])=>(
                <div key={label} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:'14px 18px'}}>
                  <div style={{fontSize:11,color:C.muted,textTransform:'uppercase',letterSpacing:1,marginBottom:6}}>{label}</div>
                  <div style={{fontFamily:FMONO,fontWeight:700,fontSize:16,color}}>{val}</div>
                </div>
              ))}
            </div>
            <div style={{background:health.vault?.ok?C.green+'12':C.red+'12',border:`1px solid ${health.vault?.ok?C.green:C.red}44`,borderRadius:8,padding:'12px 16px',display:'flex',gap:10,alignItems:'center'}}>
              {health.vault?.ok?<CheckCircle size={14} color={C.green}/>:<XCircle size={14} color={C.red}/>}
              <span style={{fontFamily:FMONO,fontSize:12,color:health.vault?.ok?C.green:C.red}}>
                Vault: {health.vault?.mode} — {health.vault?.ok?'connecté':'erreur'}{health.vault?.error?` (${health.vault.error})`:''}
                {health.vault?.key_stored!==undefined&&` · clé: ${health.vault.key_stored?'stockée':'absente'}`}
              </span>
            </div>
          </>}
        </div>}

        <div style={{display:'flex',justifyContent:'flex-end',alignItems:'center',gap:14,marginTop:24,paddingTop:20,borderTop:`1px solid ${C.border}`}}>
          {saved&&<span style={{color:C.green,fontFamily:FMONO,fontSize:13,display:'flex',alignItems:'center',gap:6}}><CheckCircle size={14}/> Variables copiées — à définir dans .env</span>}
          <Btn variant="ghost" onClick={()=>{
            const lines = Object.entries(env).filter(([,v])=>v).map(([k,v])=>`${k}=${v}`).join('\n')
            navigator.clipboard.writeText(lines).then(()=>{setSaved(true);setTimeout(()=>setSaved(false),3000)})
          }}>Copier .env</Btn>
        </div>
      </div>
    </div>
  )
}

// ── Login ─────────────────────────────────────────────────────────────────────
function Login({onLogin}) {
  const [u,setU] = useState(''); const [p,setP] = useState('')
  const [err,setErr] = useState(null); const [loading,setLoading] = useState(false)

  const submit = async () => {
    setLoading(true); setErr(null)
    try {
      const res = await api.auth.login(u, p)
      api.setToken(res.access_token)
      onLogin(res)
    } catch(e) { setErr(e.message) }
    finally { setLoading(false) }
  }

  return (
    <div style={{minHeight:'100vh',background:C.bg,display:'flex',alignItems:'center',justifyContent:'center'}}>
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:20,padding:40,width:380,boxShadow:'0 32px 80px #000c'}}>
        <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:32}}>
          <div style={{background:C.blue+'20',border:`1px solid ${C.blue}44`,borderRadius:12,padding:12}}><Terminal size={24} color={C.blue}/></div>
          <div>
            <div style={{fontFamily:FUI,fontWeight:800,fontSize:20,color:C.text}}>MCP SSH Gateway</div>
            <div style={{fontFamily:FMONO,fontSize:11,color:C.blue,marginTop:2}}>ENTERPRISE v2.0</div>
          </div>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:14}}>
          {err&&<Alert msg={err}/>}
          <div style={{display:'flex',flexDirection:'column',gap:6}}>
            <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Utilisateur</label>
            <Inp value={u} onChange={setU} placeholder="sylvain"/>
          </div>
          <div style={{display:'flex',flexDirection:'column',gap:6}}>
            <label style={{fontSize:12,color:C.muted,fontFamily:FUI}}>Mot de passe</label>
            <Inp value={p} onChange={setP} placeholder="••••••••" type="password"/>
          </div>
          <Btn onClick={submit} loading={loading} icon={Lock}>Connexion</Btn>
          <p style={{fontFamily:FMONO,fontSize:11,color:C.muted,textAlign:'center',marginTop:4}}>admin/admin123 · ops/ops123 · dev/dev123</p>
        </div>
      </div>
    </div>
  )
}

// ── App Shell ─────────────────────────────────────────────────────────────────
export default function App() {
  const [user,  setUser]  = useState(null)
  const [view,  setView]  = useState('dashboard')
  const [hOk,   setHOk]   = useState(null)  // gateway health

  useEffect(() => {
    api.onUnauth(() => setUser(null))
  }, [])

  useEffect(() => {
    if(!user) return
    api.health().then(h => setHOk(h.status==='ok')).catch(() => setHOk(false))
  }, [user])

  if(!user) return <Login onLogin={res=>setUser(res)}/>

  const nav = [
    {id:'dashboard', icon:Activity, label:'Dashboard'},
    {id:'hosts',     icon:Server,   label:'Hôtes'},
    {id:'policies',  icon:Shield,   label:'Policies'},
    {id:'audit',     icon:FileText, label:'Audit'},
    {id:'settings',  icon:Settings, label:'Configuration'},
  ]
  const isAdmin = user.roles?.includes('admin')

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Outfit:wght@400;600;800&display=swap');
        *{box-sizing:border-box;margin:0;padding:0;}
        ::-webkit-scrollbar{width:6px;height:6px;}
        ::-webkit-scrollbar-track{background:transparent;}
        ::-webkit-scrollbar-thumb{background:${C.border};border-radius:3px;}
        select option{background:${C.surface};}
        @keyframes spin{to{transform:rotate(360deg)}}
      `}</style>
      <div style={{display:'flex',height:'100vh',background:C.bg,color:C.text,fontFamily:FUI,overflow:'hidden'}}>
        <aside style={{width:230,background:C.surface,borderRight:`1px solid ${C.border}`,display:'flex',flexDirection:'column',flexShrink:0}}>
          <div style={{padding:'24px 20px 20px',borderBottom:`1px solid ${C.border}`}}>
            <div style={{display:'flex',alignItems:'center',gap:10}}>
              <div style={{background:C.blue+'20',border:`1px solid ${C.blue}44`,borderRadius:10,padding:8}}><Terminal size={18} color={C.blue}/></div>
              <div><div style={{fontFamily:FUI,fontWeight:800,fontSize:14,color:C.text}}>MCP SSH</div><div style={{fontSize:10,color:C.blue,fontFamily:FMONO,marginTop:2}}>GATEWAY v2.0</div></div>
            </div>
          </div>
          <nav style={{flex:1,padding:'16px 12px',display:'flex',flexDirection:'column',gap:4}}>
            {nav.filter(n=>n.id!=='settings'||isAdmin).map(n=>(
              <button key={n.id} onClick={()=>setView(n.id)} style={{display:'flex',alignItems:'center',gap:10,width:'100%',padding:'10px 12px',borderRadius:10,border:'none',cursor:'pointer',transition:'all 0.15s',textAlign:'left',background:view===n.id?C.blue+'20':'transparent',color:view===n.id?C.blue:C.muted,fontFamily:FUI,fontWeight:view===n.id?600:400,fontSize:14}}>
                <n.icon size={16}/>{n.label}
                {view===n.id&&<div style={{marginLeft:'auto',width:4,height:4,borderRadius:'50%',background:C.blue}}/>}
              </button>
            ))}
          </nav>
          <div style={{padding:'16px 20px',borderTop:`1px solid ${C.border}`,display:'flex',flexDirection:'column',gap:10}}>
            <div style={{display:'flex',alignItems:'center',gap:8}}>
              {hOk===null?<Clock size={14} color={C.muted}/>:hOk?<Wifi size={14} color={C.green}/>:<WifiOff size={14} color={C.red}/>}
              <div>
                <div style={{fontSize:12,fontFamily:FUI,color:C.text,fontWeight:600}}>{user.username}</div>
                <div style={{fontSize:10,fontFamily:FMONO,color:C.muted}}>{(user.roles||[]).join(', ')}</div>
              </div>
            </div>
            <button onClick={()=>{api.clearToken();setUser(null)}} style={{display:'flex',alignItems:'center',gap:8,background:'none',border:`1px solid ${C.border}`,borderRadius:8,padding:'7px 12px',color:C.muted,cursor:'pointer',fontFamily:FUI,fontSize:12,width:'100%'}}>
              <LogOut size={13}/>Déconnexion
            </button>
          </div>
        </aside>
        <main style={{flex:1,overflow:'auto',padding:32}}>
          {view==='dashboard'&&<Dashboard/>}
          {view==='hosts'    &&<HostsView/>}
          {view==='policies' &&<PoliciesView/>}
          {view==='audit'    &&<AuditView/>}
          {view==='settings' &&<SettingsView/>}
        </main>
      </div>
    </>
  )
}
