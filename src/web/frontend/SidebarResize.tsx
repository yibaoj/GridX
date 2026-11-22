import {useEffect,useRef,useState} from 'react';

const minimum=180;
const standard=200;
const maximum=320;
const storageKey='gridx.sidebarWidth';
const clamp=(value:number,upper:number)=>Math.min(upper,Math.max(minimum,Math.round(value)));
const viewportLimit=()=>clamp(window.innerWidth-520,maximum);

function savedWidth() {
  try {
    const value=localStorage.getItem(storageKey);
    if(value!==null && Number.isFinite(Number(value))) return clamp(Number(value),maximum);
  } catch {
    // The sidebar still works in a browser that blocks local storage.
  }
  return standard;
}

export function useSidebarWidth() {
  const [preferred,setPreferred]=useState(savedWidth);
  const [limit,setLimit]=useState(viewportLimit);
  useEffect(()=>{
    const update=()=>setLimit(viewportLimit());
    window.addEventListener('resize',update);
    return ()=>window.removeEventListener('resize',update);
  },[]);
  const commit=(value:number)=>{
    const next=clamp(value,maximum);
    setPreferred(next);
    try {localStorage.setItem(storageKey,String(next));} catch { /* Session-only preference. */ }
  };
  return {width:clamp(preferred,limit),limit,resize:setPreferred,commit};
}

export default function SidebarResize({width,limit,resize,commit}:ReturnType<typeof useSidebarWidth>) {
  const drag=useRef<{pointerId:number;startX:number;startWidth:number;value:number}>(null);
  const [dragging,setDragging]=useState(false);
  const finish=(pointerId:number)=>{
    const active=drag.current;
    if(!active || active.pointerId!==pointerId) return;
    drag.current=null;
    setDragging(false);
    if(active.value!==active.startWidth) commit(active.value);
  };

  return <div className={`sidebar-resizer${dragging?' dragging':''}`} role="separator" tabIndex={0}
    aria-label="调整侧栏宽度" aria-orientation="vertical" aria-controls="site-navigation"
    aria-valuemin={minimum} aria-valuemax={limit} aria-valuenow={width} aria-valuetext={`${width} 像素`}
    title="拖动或按左右方向键调节，双击恢复默认"
    onPointerDown={event=>{
      if(event.button!==0 || !event.isPrimary) return;
      event.preventDefault();
      event.currentTarget.focus({preventScroll:true});
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current={pointerId:event.pointerId,startX:event.clientX,startWidth:width,value:width};
      setDragging(true);
    }}
    onPointerMove={event=>{
      const active=drag.current;
      if(!active || active.pointerId!==event.pointerId) return;
      const next=clamp(active.startWidth+event.clientX-active.startX,limit);
      if(next!==active.value) {
        active.value=next;
        resize(next);
      }
    }}
    onPointerUp={event=>finish(event.pointerId)} onPointerCancel={event=>finish(event.pointerId)}
    onLostPointerCapture={event=>finish(event.pointerId)} onDoubleClick={()=>commit(standard)}
    onKeyDown={event=>{
      const next={ArrowLeft:width-16,ArrowRight:width+16,Home:minimum,End:limit}[event.key];
      if(next===undefined) return;
      event.preventDefault();
      commit(clamp(next,limit));
    }}/>
}
