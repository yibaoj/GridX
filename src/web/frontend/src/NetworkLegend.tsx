import type {NetworkFilter} from './types';
import {AUTO_NETWORK,networkVoltageLevels} from './networkFilter';
import {format} from './data';
import TermHelp from './TermHelp';

type VoltageLevels=ReturnType<typeof networkVoltageLevels>;
const commonVoltages=new Set([110,220,330,500,660,750,800,1000,1100]);

interface NetworkControls {
  voltages:number[];
  filter:NetworkFilter;
  onChange:(filter:NetworkFilter)=>void;
}

export function NetworkActions({voltages,filter,onChange,label}:NetworkControls & {label:string}) {
  const manual=filter.mode==='manual';
  return <div className="voltage-actions" role="group" aria-label={label}>
    <button type="button" aria-pressed={!manual} onClick={()=>onChange(AUTO_NETWORK)}>自动</button>
    <button type="button" disabled={!voltages.length}
      aria-pressed={manual && voltages.length>0 && filter.voltages.length===voltages.length}
      onClick={()=>onChange({mode:'manual',voltages})}>全选</button>
    <button type="button" aria-pressed={manual && filter.voltages.length===0}
      onClick={()=>onChange({mode:'manual',voltages:[]})}>清空</button>
  </div>;
}

export default function NetworkLegend({levels,voltages,filter,visible,minimum,onChange}:NetworkControls & {
  levels:VoltageLevels;
  visible:ReadonlySet<number>;
  minimum:number;
}) {
  const common=levels.filter(level=>commonVoltages.has(level.voltage));
  const other=levels.filter(level=>!commonVoltages.has(level.voltage));
  const toggle=(voltage:number)=>{
    const next=new Set(visible);
    if(next.has(voltage)) next.delete(voltage);
    else next.add(voltage);
    onChange({mode:'manual',voltages:[...next]});
  };
  const rows=(items:VoltageLevels)=>items.map(({voltage,color})=>{
    const label=`${format(voltage,2)} kV`;
    return <div className="voltage-row" key={voltage}>
      <button type="button" className="voltage-name" aria-label={`仅显示 ${label}`}
        aria-pressed={filter.mode==='manual' && visible.size===1 && visible.has(voltage)}
        onClick={()=>onChange({mode:'manual',voltages:[voltage]})}>
        <i className="voltage-swatch" style={{backgroundColor:color}} aria-hidden="true"/>
        <span>{label}</span>
      </button>
      <label className="voltage-check"><input type="checkbox" aria-label={`包含 ${label}`}
        checked={visible.has(voltage)} onChange={()=>toggle(voltage)}/></label>
    </div>;
  });
  return <>
    <div className="legend-actions-row">
      <NetworkActions voltages={voltages} filter={filter} onChange={onChange} label="图例电压筛选"/>
      <TermHelp term="autoVoltage" label="电压筛选"/>
    </div>
    <div className="voltage-levels">{rows(common)}</div>
    {other.length>0 && <details className="more-voltages">
      <summary>更多电压（{other.length}）</summary>
      <div className="voltage-levels">{rows(other)}</div>
    </details>}
    <small className="voltage-status" role="status">{filter.mode==='auto'
      ? (minimum ? `自动 · 当前 ≥ ${minimum} kV` : '自动 · 全部电压')
      : `手动 · 已选 ${visible.size} 档`}</small>
  </>;
}
