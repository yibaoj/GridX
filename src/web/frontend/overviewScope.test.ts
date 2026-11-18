import {describe,it,expect} from 'vitest';
import {overviewRoute,readRoute,provincialStats,overviewExport} from './overviewScope';
import type {Overview,Province} from './types';
const provinces:Province[]=[{id:'figshare:广东/1',name:'广东省',value:850.123456789,unit:'TWh',bounds:[110,20,117,25]},{id:'figshare:江苏/2',name:'江苏省',value:800,unit:'TWh',bounds:[116,30,122,35]}];
const overview={provinces,year:2024,annual_energy_twh:1650.123456789} as Overview;
describe('provincial context and original values',()=>{
  it('round trips a stable source ID through a bookmark',()=>{expect(readRoute('#'+overviewRoute(provinces[0]))).toEqual({page:'overview',provinceId:provinces[0].id});expect(readRoute('#map')).toEqual({page:'map',provinceId:''});expect(readRoute('#unknown')).toEqual({page:'overview',provinceId:''});});
  it('uses the same full-year denominator and stable national ranking',()=>{const stats=provincialStats(overview,provinces[1]);expect(stats.rank).toBe(2);expect(stats.share).toBe(800/overview.annual_energy_twh*100);expect(provincialStats({...overview,provinces:[...provinces].reverse()},provinces[0]).rank).toBe(1);});
  it('exports one selected province at original precision and all provinces nationally',()=>{const file=overviewExport(overview,provinces[0]);expect(file.name).toBe('GridX-2024-广东省用电量.csv');expect(file.csv.split('\r\n')).toEqual(['地区,年度用电量 (TWh),年份','广东省,850.123456789,2024']);expect(overviewExport(overview).csv.split('\r\n')).toHaveLength(3);});
});
