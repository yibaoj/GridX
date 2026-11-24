import {useEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import {ArrowUpRight,ChartNoAxesColumnIncreasing,ChevronRight,MapPinned,Menu,Radio,X,Zap,ChartSpline} from 'lucide-react';
import type {Bootstrap,Overview as OverviewData,Page,Province,MapSettings} from './types';
import {useResource} from './data';
import Overview from './Overview';
import Explorer,{defaultSettings} from './Explorer';
import Simulation from './Simulation';
import News from './News';
import {Loading,ErrorState,useModalFocus} from './ui';
import {readRoute,overviewRoute} from './overviewScope';
import SidebarResize,{useSidebarWidth} from './SidebarResize';
const navigation=[{id:'overview' as Page,label:'数据总览',icon:ChartNoAxesColumnIncreasing},{id:'map' as Page,label:'电力地图',icon:MapPinned},{id:'simulation' as Page,label:'运行模拟',icon:ChartSpline},{id:'learn' as Page,label:'新闻消息',icon:Radio}];
export default function App(){
  const config=useResource<Bootstrap>('/api/bootstrap');const overview=useResource<OverviewData>('/api/overview');
  const sidebarWidth=useSidebarWidth();
  const [page,setPage]=useState<Page>(()=>readRoute(location.hash).page);const [settings,setSettings]=useState<MapSettings>(defaultSettings);const [province,setProvince]=useState<Province>();
  const [overviewProvinceId,setOverviewProvinceId]=useState(()=>readRoute(location.hash).provinceId);const [sidebar,setSidebar]=useState(false);const [simulationModal,setSimulationModal]=useState(false);
  const [mobile,setMobile]=useState(()=>matchMedia('(max-width:700px)').matches);const drawer=useRef<HTMLElement>(null);const backdrop=useRef<HTMLButtonElement>(null);const menu=useRef<HTMLButtonElement>(null);const workspace=useRef<HTMLElement>(null);const navigationReturn=useRef<HTMLElement|null>(null);
  useEffect(()=>{const media=matchMedia('(max-width:700px)');const changed=()=>{setMobile(media.matches);setSidebar(false);};media.addEventListener('change',changed);return()=>media.removeEventListener('change',changed);},[]);
  const pageFocus=()=>workspace.current;
  useModalFocus(mobile&&sidebar,drawer,()=>setSidebar(false),()=>navigationReturn.current?.getClientRects().length?navigationReturn.current:pageFocus(),backdrop);
  useEffect(()=>{const changed=()=>{const route=readRoute(location.hash);setPage(route.page);setOverviewProvinceId(route.provinceId);};const escape=(e:KeyboardEvent)=>{if(!e.defaultPrevented&&e.key==='Escape')setSidebar(false);};window.addEventListener('hashchange',changed);window.addEventListener('keydown',escape);return()=>{window.removeEventListener('hashchange',changed);window.removeEventListener('keydown',escape);};},[]);
  const navigate=(next:Page,hash:string=next)=>{if(mobile&&sidebar)navigationReturn.current=null;location.hash=hash;setPage(next);setSidebar(false);window.scrollTo({top:0});};
  const openProvince=(p?:Province)=>{setOverviewProvinceId(p?.id||'');navigate('overview',overviewRoute(p));};const selectedProvince=overview.data?.provinces.find(p=>p.id===overviewProvinceId);
  const openRegionalMap=(p?:Province,layer:MapSettings['layer']='load')=>{setSettings({...defaultSettings,layer});setProvince(p);navigate('map');};const active=navigation.find(n=>n.id===page)!;
  return <div className="app-shell" style={{'--sidebar-width':`${sidebarWidth.width}px`} as CSSProperties}><a className="skip-link" href="#main-content" onClick={e=>{e.preventDefault();document.getElementById('main-content')?.focus();}}>跳到主要内容</a>{mobile&&sidebar&&<button ref={backdrop} tabIndex={-1} className="sidebar-backdrop" aria-label="关闭导航" onClick={()=>setSidebar(false)}/>}
    <aside ref={drawer} id="site-navigation" tabIndex={-1} inert={simulationModal||(mobile&&!sidebar)} role={mobile&&sidebar?'dialog':undefined} aria-modal={mobile&&sidebar?true:undefined} aria-label={mobile&&sidebar?'网站导航':undefined} className={`sidebar ${sidebar?'open':''}`}>
      <div className="sidebar-brand-row"><a className="brand" href="#overview" onClick={()=>navigate('overview')} aria-label="GridX 网探首页"><span className="brand-mark"><Zap size={23} fill="currentColor"/></span><strong>GridX<span>网探</span></strong></a>{mobile&&sidebar&&<button className="icon-button" aria-label="关闭导航菜单" onClick={()=>setSidebar(false)}><X size={20}/></button>}</div>
      <nav aria-label="主导航">{navigation.map(item=><a key={item.id} href={`#${item.id}`} onClick={()=>navigate(item.id)} aria-current={page===item.id?'page':undefined} className={page===item.id?'active':''}><item.icon size={20} strokeWidth={1.75} aria-hidden="true"/><span>{item.label}</span>{page===item.id&&<ChevronRight size={15}/>}</a>)}</nav>
      <div className="sidebar-bottom"><a href="/legacy/" target="_blank" rel="noreferrer">打开原版界面<ArrowUpRight size={14}/></a></div>
    </aside>
    {!mobile&&!simulationModal&&<SidebarResize {...sidebarWidth}/>}
    <main ref={workspace} className="main-content" id="main-content" tabIndex={-1} aria-label={active.label}>
      <div className={`page-content page-${page}`}>
      {mobile && <div className="mobile-navigation">
        <button ref={menu} className="icon-button" aria-label="打开导航" aria-controls="site-navigation" aria-expanded={sidebar} onClick={()=>{navigationReturn.current=menu.current;setSidebar(!sidebar);}}><Menu size={20}/></button>
      </div>}
      {config.error?<ErrorState message={config.error} retry={config.retry}/>:config.data&&!config.data.data_status.available?<ErrorState message={config.data.data_status.message} retry={()=>location.reload()}/>:overview.error?<ErrorState message={overview.error} retry={overview.retry}/>:!config.data||!overview.data?<Loading text="正在加载数据…"/>:<>
        {page==='overview'&&<Overview config={config.data} data={overview.data} province={selectedProvince} onProvince={openProvince} onMap={openRegionalMap} onSimulation={()=>navigate('simulation')}/>}
        {page==='map'&&<Explorer config={config.data} overview={overview.data} settings={settings} setSettings={setSettings} province={province} onProvinceConsumed={()=>setProvince(undefined)}/>}
        {page==='simulation'&&<Simulation onExpandedChange={setSimulationModal}/>}{page==='learn'&&<News/>}</>}
    </div></main></div>;
}
