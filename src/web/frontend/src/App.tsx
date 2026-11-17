import {useEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import {Activity,ArrowUpRight,CalendarDays,ChevronRight,Download,LayoutDashboard,Map,Menu,Newspaper,X,Zap} from 'lucide-react';
import type {Bootstrap,Overview as OverviewData,Page,Province,MapSettings} from './types';
import {useResource,download} from './data';
import Overview from './Overview';
import Explorer,{defaultSettings} from './Explorer';
import Simulation from './Simulation';
import Learn from './Learn';
import News from './News';
import {Loading,ErrorState,useModalFocus} from './ui';
import {readRoute,overviewRoute,overviewExport} from './overviewScope';
import SidebarResize,{useSidebarWidth} from './SidebarResize';
const navigation=[{id:'overview' as Page,label:'数据总览',icon:LayoutDashboard},{id:'map' as Page,label:'电力地图',icon:Map},{id:'simulation' as Page,label:'运行模拟',icon:Activity},{id:'learn' as Page,label:'新闻消息',icon:Newspaper}];
export default function App(){
  const config=useResource<Bootstrap>('/api/bootstrap');const overview=useResource<OverviewData>('/api/overview');
  const sidebarWidth=useSidebarWidth();
  const [page,setPage]=useState<Page>(()=>readRoute(location.hash).page);const [settings,setSettings]=useState<MapSettings>(defaultSettings);const [province,setProvince]=useState<Province>();
  const [overviewProvinceId,setOverviewProvinceId]=useState(()=>readRoute(location.hash).provinceId);const [sidebar,setSidebar]=useState(false);const [simulationModal,setSimulationModal]=useState(false);
  const [mobile,setMobile]=useState(()=>matchMedia('(max-width:700px)').matches);const drawer=useRef<HTMLElement>(null);const backdrop=useRef<HTMLButtonElement>(null);const menu=useRef<HTMLButtonElement>(null);const heading=useRef<HTMLHeadingElement>(null);const regionPicker=useRef<HTMLSelectElement>(null);const navigationReturn=useRef<HTMLElement|null>(null);
  useEffect(()=>{const media=matchMedia('(max-width:700px)');const changed=()=>{setMobile(media.matches);setSidebar(false);};media.addEventListener('change',changed);return()=>media.removeEventListener('change',changed);},[]);
  const pageFocus=()=>page==='overview' ? regionPicker.current : heading.current;
  useModalFocus(mobile&&sidebar,drawer,()=>setSidebar(false),()=>navigationReturn.current?.getClientRects().length?navigationReturn.current:pageFocus(),backdrop);
  useEffect(()=>{const changed=()=>{const route=readRoute(location.hash);setPage(route.page);setOverviewProvinceId(route.provinceId);};const escape=(e:KeyboardEvent)=>{if(!e.defaultPrevented&&e.key==='Escape')setSidebar(false);};window.addEventListener('hashchange',changed);window.addEventListener('keydown',escape);return()=>{window.removeEventListener('hashchange',changed);window.removeEventListener('keydown',escape);};},[]);
  const navigate=(next:Page,hash:string=next)=>{if(mobile&&sidebar)navigationReturn.current=null;location.hash=hash;setPage(next);setSidebar(false);window.scrollTo({top:0});};
  const openProvince=(p?:Province)=>{setOverviewProvinceId(p?.id||'');navigate('overview',overviewRoute(p));};const selectedProvince=overview.data?.provinces.find(p=>p.id===overviewProvinceId);const regionName=selectedProvince?.name||'全国';
  const openRegionalMap=(p?:Province,layer:MapSettings['layer']='load')=>{setSettings({...defaultSettings,layer});setProvince(p);navigate('map');};const openMap=(patch:Partial<MapSettings>={})=>{setSettings({...defaultSettings,...patch});navigate('map');};const active=navigation.find(n=>n.id===page)!;
  const exportOverview=()=>{if(overview.data){const file=overviewExport(overview.data,selectedProvince);download(file.name,file.csv);}};
  return <div className="app-shell" style={{'--sidebar-width':`${sidebarWidth.width}px`} as CSSProperties}><a className="skip-link" href="#main-content" onClick={e=>{e.preventDefault();document.getElementById('main-content')?.focus();}}>跳到主要内容</a>{mobile&&sidebar&&<button ref={backdrop} tabIndex={-1} className="sidebar-backdrop" aria-label="关闭导航" onClick={()=>setSidebar(false)}/>}
    <aside ref={drawer} id="site-navigation" tabIndex={-1} inert={simulationModal||(mobile&&!sidebar)} role={mobile&&sidebar?'dialog':undefined} aria-modal={mobile&&sidebar?true:undefined} aria-label={mobile&&sidebar?'网站导航':undefined} className={`sidebar ${sidebar?'open':''}`}>
      <div className="sidebar-brand-row"><a className="brand" href="#overview" onClick={()=>navigate('overview')} aria-label="GridX 网探首页"><span className="brand-mark"><Zap size={23} fill="currentColor"/></span><strong>GridX<span>网探</span></strong></a>{mobile&&sidebar&&<button className="icon-button" aria-label="关闭导航菜单" onClick={()=>setSidebar(false)}><X size={20}/></button>}</div>
      <nav aria-label="主导航">{navigation.map(item=><a key={item.id} href={`#${item.id}`} onClick={()=>navigate(item.id)} aria-current={page===item.id?'page':undefined} className={page===item.id?'active':''}><item.icon size={19}/><span>{item.label}</span>{page===item.id&&<ChevronRight size={15}/>}</a>)}</nav>
      <div className="sidebar-bottom"><a href="/legacy/" target="_blank" rel="noreferrer">打开原版界面<ArrowUpRight size={14}/></a></div>
    </aside>
    {!mobile&&!simulationModal&&<SidebarResize {...sidebarWidth}/>}
    <main className="main-content" id="main-content" tabIndex={-1}><div className={`page-content page-${page}`}><div className="page-heading"><div className="heading-main">
      <button ref={menu} className="icon-button menu-button" aria-label="打开导航" aria-controls="site-navigation" aria-expanded={sidebar} onClick={()=>{navigationReturn.current=menu.current;setSidebar(!sidebar);}}><Menu size={20}/></button>
      {page==='overview'?<div className="overview-title"><h1 className="sr-only">{regionName}电力系统总览</h1><label className="region-picker"><span className="sr-only">选择总览地区</span><select ref={regionPicker} aria-label="选择总览地区" disabled={!overview.data} value={selectedProvince?.id||''} onChange={e=>openProvince(overview.data?.provinces.find(p=>p.id===e.target.value))} style={{width:`${Math.max(100,regionName.length*25+32)}px`}}><option value="">全国</option>{overview.data?.provinces.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label></div>:<h1 ref={heading} tabIndex={-1}>{active.label}</h1>}
      </div>{page!=='learn'&&<div className="page-actions">{page==='overview'?<label className="year-picker"><span className="sr-only">选择年份</span><select key={overview.data?.year??config.data?.manifest.year??''} aria-label="选择年份" defaultValue={overview.data?.year??config.data?.manifest.year??''} title="年份来自本地导出清单"><option value={overview.data?.year??config.data?.manifest.year??''}>{overview.data?.year??config.data?.manifest.year??''} 年</option></select></label>:<span className="date-chip"><CalendarDays size={15}/>{overview.data?.year??config.data?.manifest.year??''} 年</span>}{page==='overview'&&<button className="button" onClick={exportOverview} disabled={!overview.data}><Download size={15}/>导出数据</button>}</div>}</div>
      {config.error?<ErrorState message={config.error} retry={config.retry}/>:config.data&&!config.data.data_status.available?<ErrorState message={config.data.data_status.message} retry={()=>location.reload()}/>:overview.error?<ErrorState message={overview.error} retry={overview.retry}/>:!config.data||!overview.data?<Loading text="正在加载数据…"/>:<>
        {page==='overview'&&<Overview config={config.data} data={overview.data} province={selectedProvince} onProvince={openProvince} onMap={openRegionalMap} onSimulation={()=>navigate('simulation')}/>}
        {page==='map'&&<Explorer config={config.data} overview={overview.data} settings={settings} setSettings={setSettings} province={province} onProvinceConsumed={()=>setProvince(undefined)}/>}
        {page==='simulation'&&<Simulation onExpandedChange={setSimulationModal}/>}{page==='learn'&&<><News/><details className="news-data-reference"><summary>数据说明与电力知识</summary><Learn data={overview.data} onMap={openMap}/></details></>}</>}
    </div></main></div>;
}
