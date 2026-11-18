import {useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Info} from 'lucide-react';
import {glossary} from './glossary';
import type {GlossaryTerm} from './glossary';

export default function TermHelp({term,label}:{term:GlossaryTerm;label?:string}) {
  const entry=glossary[term];const id=useId();
  const trigger=useRef<HTMLButtonElement>(null);const popup=useRef<HTMLDivElement>(null);const timer=useRef<number|undefined>(undefined);
  const[hovered,setHovered]=useState(false);const[focused,setFocused]=useState(false);const[pinned,setPinned]=useState(false);const[dismissed,setDismissed]=useState(false);
  const[position,setPosition]=useState({left:12,top:12});
  const[maxHeight,setMaxHeight]=useState(window.innerHeight-24);
  const open=!dismissed&&(hovered||focused||pinned);
  const enter=()=>{window.clearTimeout(timer.current);setHovered(true);setDismissed(false);};
  const leave=()=>{window.clearTimeout(timer.current);timer.current=window.setTimeout(()=>setHovered(false),120);};
  const close=()=>{setPinned(false);setDismissed(true);};
  useEffect(()=>()=>window.clearTimeout(timer.current),[]);
  useLayoutEffect(()=>{
    if(!open)return;
    const place=()=>{
      const anchor=trigger.current?.getBoundingClientRect();const box=popup.current?.getBoundingClientRect();if(!anchor||!box)return;
      const below=window.innerHeight-anchor.bottom-20;const above=anchor.top-20;
      let height=Math.min(popup.current!.scrollHeight,window.innerHeight-24);
      let left=anchor.left+anchor.width/2-box.width/2;let top:number;
      if(height>Math.max(above,below)&&(window.innerWidth-anchor.right-20>=box.width||anchor.left-20>=box.width)){
        left=window.innerWidth-anchor.right-20>=box.width?anchor.right+8:anchor.left-box.width-8;top=anchor.top;
      }else{
        height=Math.min(height,Math.max(60,above,below));
        top=below>=height||below>=above?anchor.bottom+8:anchor.top-height-8;
      }
      setMaxHeight(height);
      setPosition({left:Math.max(12,Math.min(left,window.innerWidth-box.width-12)),top:Math.max(12,Math.min(top,window.innerHeight-height-12))});
    };
    place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
    return()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);};
  },[open,entry]);
  useEffect(()=>{
    if(!open)return;
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}};
    const outside=(event:PointerEvent)=>{const target=event.target as Node;if(!trigger.current?.contains(target)&&!popup.current?.contains(target))close();};
    document.addEventListener('keydown',escape,true);document.addEventListener('pointerdown',outside);
    return()=>{document.removeEventListener('keydown',escape,true);document.removeEventListener('pointerdown',outside);};
  },[open]);
  return <span className="term-help"><button ref={trigger} type="button" className="term-help-trigger" aria-label={`了解${label||entry.title}`} aria-expanded={open} aria-describedby={open?id:undefined} onMouseEnter={enter} onMouseLeave={leave} onFocus={()=>{setFocused(true);setDismissed(false);}} onBlur={()=>setFocused(false)} onClick={event=>{event.preventDefault();setPinned(!pinned);setDismissed(pinned);}} onKeyDown={event=>{
      const box=popup.current;if(!open||!box||box.scrollHeight<=box.clientHeight||event.altKey||event.ctrlKey||event.metaKey)return;
      const steps:Record<string,number>={ArrowDown:40,ArrowUp:-40,PageDown:box.clientHeight,PageUp:-box.clientHeight,Home:-box.scrollHeight,End:box.scrollHeight};
      if(event.key in steps){event.preventDefault();box.scrollTop+=steps[event.key];}
    }}><Info size={16} aria-hidden="true"/></button>
    {open&&createPortal(<div ref={popup} id={id} role="tooltip" className="term-help-popup" style={{...position,maxHeight}} onMouseEnter={enter} onMouseLeave={leave}><strong>{entry.title}</strong>{entry.paragraphs.map(text=><p key={text}>{text}</p>)}</div>,document.body)}
  </span>;
}
