import {useLayoutEffect,useRef} from 'react';
import type {RefObject} from 'react';
import {AlertCircle,RefreshCw} from 'lucide-react';
export function useModalFocus(active:boolean,container:RefObject<HTMLElement|null>,onClose:()=>void,returnFocus:()=>HTMLElement|null,outside?:RefObject<HTMLElement|null>){
  const callbacks=useRef({onClose,returnFocus});callbacks.current={onClose,returnFocus};
  const restore=useRef(false);
  useLayoutEffect(()=>{
    if(!active){if(restore.current)callbacks.current.returnFocus()?.focus();restore.current=false;return;}
    const root=container.current;if(!root)return;
    const background=new Map<HTMLElement,boolean>();
    // Disable siblings along the ancestor path, never an ancestor of the dialog.
    for(let branch:HTMLElement=root;branch.parentElement;branch=branch.parentElement){
      for(const sibling of branch.parentElement.children){
        if(sibling instanceof HTMLElement&&sibling!==branch&&sibling!==outside?.current&&!sibling.inert){background.set(sibling,false);sibling.inert=true;}
      }
      if(branch.parentElement===document.body)break;
    }
    const overflow=document.body.style.overflow;document.body.style.overflow='hidden';
    const controls=()=>Array.from(root.querySelectorAll<HTMLElement>('a[href],button,input,select,textarea,summary,[tabindex]')).filter(element=>element.tabIndex>=0&&!element.matches(':disabled')&&!element.closest('[inert]')&&element.getClientRects().length>0&&getComputedStyle(element).visibility!=='hidden');
    const focusFirst=()=>{(controls()[0]||root).focus();};
    const keydown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();callbacks.current.onClose();}
      if(event.key==='Tab'){
        const items=controls();const first=items[0];const last=items.at(-1);
        if(!first){event.preventDefault();root.focus();}
        else if(event.shiftKey&&(document.activeElement===first||document.activeElement===root)||!event.shiftKey&&(document.activeElement===last||!root.contains(document.activeElement))){event.preventDefault();(event.shiftKey?last:first)?.focus();}
      }
    };
    const focusin=()=>{if(!root.contains(document.activeElement))focusFirst();};
    document.addEventListener('keydown',keydown);document.addEventListener('focusin',focusin);focusFirst();
    return()=>{
      document.removeEventListener('keydown',keydown);document.removeEventListener('focusin',focusin);
      background.forEach((inert,element)=>{element.inert=inert;});document.body.style.overflow=overflow;
      restore.current=true;
    };
  },[active,container,outside]);
}
export function Loading({text='正在读取数据…'}:{text?:string}){return <div className="loading-state" role="status"><div className="skeleton"/><div className="skeleton short"/><span>{text}</span></div>;}
export function ErrorState({message,retry}:{message:string;retry:()=>void}){return <div className="error-state" role="alert"><AlertCircle size={24}/><h2>数据暂不可用</h2><p>{message}</p><button className="button" onClick={retry}><RefreshCw size={15}/>重试</button><small>请确认本地服务与网页数据包可用。</small></div>;}
