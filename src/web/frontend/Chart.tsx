import {useEffect,useRef} from 'react';
import * as echarts from 'echarts/core';
import {LineChart,BarChart,PieChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import type {EChartsCoreOption,EChartsType} from 'echarts/core';
echarts.use([LineChart,BarChart,PieChart,GridComponent,TooltipComponent,LegendComponent,DataZoomComponent,TitleComponent,AriaComponent,CanvasRenderer]);

export function renderChartPng(option:EChartsCoreOption,width:number,height:number) {
  const canvas=document.createElement('canvas');
  const instance=echarts.init(canvas,undefined,{renderer:'canvas',width,height});
  try {
    instance.setOption({...option,animation:false});
    return instance.getDataURL({type:'png',pixelRatio:2,backgroundColor:'#ffffff'});
  } finally {
    instance.dispose();
  }
}

export default function Chart({option,label,onReady,onClick,height=280}:{option:EChartsCoreOption;label:string;onReady?:(chart:EChartsType)=>void;onClick?:(event:any)=>void;height?:number}){
  const ref=useRef<HTMLDivElement>(null);const chart=useRef<EChartsType|undefined>(undefined);const click=useRef(onClick);click.current=onClick;
  useEffect(()=>{const instance=echarts.init(ref.current);chart.current=instance;instance.on('click',event=>click.current?.(event));onReady?.(instance);const observer=new ResizeObserver(()=>instance.resize());observer.observe(ref.current!);return()=>{observer.disconnect();instance.dispose();};},[]);
  useEffect(()=>{chart.current?.setOption({animation:!matchMedia('(prefers-reduced-motion: reduce)').matches,animationDuration:200,textStyle:{fontFamily:getComputedStyle(document.documentElement).fontFamily,color:'#5d6c65'},aria:{enabled:true,label:{description:label}},...option},true);},[option,label]);
  return <div ref={ref} className="chart" role="img" aria-label={label} style={{height}}/>;
}
