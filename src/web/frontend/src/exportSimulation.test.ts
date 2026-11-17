import {it,expect} from 'vitest';
import {simulationCSV,firstDayEnd} from './exportSimulation';
import type {Application} from './types';

function result(hours:number):Application {
  return {
    time:Array.from({length:hours},(_,index)=>new Date(Date.UTC(2024,7,1,index)).toISOString()),
    series:[
      {id:'imports',kind:'generation',values:Array.from({length:hours},(_,index)=>index+0.25)},
      {id:'storage',kind:'storage_discharge',values:Array.from({length:hours},()=>2.5)},
      {id:'storage',kind:'storage_charge',values:Array.from({length:hours},()=>-1.5)},
      {id:'load',kind:'load',values:Array.from({length:hours},()=>7)},
    ],
    categories:[],summary:{count:hours},
  };
}

it('exports variable-length local results and separate charging/discharging series',()=>{
  const data=result(744);
  const csv=simulationCSV(data);
  const rows=csv.content.split('\r\n').map(row=>row.split(','));
  expect(csv.hours).toBe(744);
  expect(csv.series).toBe(4);
  expect(new Set(rows[0]).size).toBe(5);
  for(let hour=0;hour<data.time.length;hour++){
    expect(rows[hour+1][0]).toBe(data.time[hour]);
    data.series.forEach((series,index)=>expect(Number(rows[hour+1][index+1])).toBe(series.values[hour]));
  }
});

it('exports the selected first day and excludes hidden series',()=>{
  const data=result(744);
  const csv=simulationCSV(data,0,firstDayEnd(data.time.length,true),{load:false});
  const rows=csv.content.split('\r\n');
  expect(csv.hours).toBe(24);
  expect(csv.series).toBe(3);
  expect(rows[0]).not.toContain('load:load');
  expect(rows.at(-1)?.split(',')[0]).toBe(data.time[23]);
});

it('keeps short and single-snapshot results within the chart range',()=>{
  for(const hours of [1,12,24,32,35,42,63,168,744,2208]){
    const data=result(hours);
    const end=firstDayEnd(hours,true);
    expect(end).toBeGreaterThan(0);
    expect(end).toBeLessThanOrEqual(100);
    expect(simulationCSV(data,0,end).hours).toBe(Math.min(hours,24));
  }
});
