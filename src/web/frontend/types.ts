import type {Feature, Geometry} from 'geojson';
export type Stage = 'standard'|'mapping'|'case';
export type LayerId = 'generator'|'storage'|'network'|'load'|'resource'|'population';
export type Page = 'overview'|'map'|'simulation'|'learn';
export type Properties = Record<string, any>;
export type GeoFeature = Feature<Geometry,Properties>;
export interface Category {id:string;label:string;color:string;value?:number}
export interface Summary {count:number;value?:number;unit?:string;title?:string;categories?:Category[];branch_legend?:{label:string;color:string;dash:boolean}[];[key:string]:any}
export interface Payload {summary:Summary;geojson:{type:'FeatureCollection';features:GeoFeature[]}}
export interface Province {id:string;name:string;value:number;unit:string;bounds:[number,number,number,number]}
export interface Overview {year:number;historical:boolean;generated_at:string;metrics:{generator:{capacity_mw:number;records:number;drawable:number};storage:{capacity_mw:number;records:number;drawable:number};network:{buses:number;branches:number;drawable:number}};capacity_mix:Category[];provinces:Province[];province_names:Record<string,string>;annual_energy_twh:number;catalog:{stage:Stage;layer:LayerId;resource_class:string|null;feature_count:number;summary:Summary;source:string}[];sources:{path:string;sha256:string}[];application:{summary:Summary;start:string;end:string;series_count:number}}
export interface Bootstrap {site:{title:string;title_zh:string};map:{tile_url:string;tile_attribution:string;center:number[];zoom:number};manifest:{year:number;resource_classes:{id:string;label:string}[]};data_status:{available:boolean;message:string}}
export interface Application {time:string[];series:{id:string;kind:string;values:number[]}[];categories:Category[];summary:Summary}
export interface RegionalIndicators {installed_capacity_mw:number;new_energy_capacity_mw:number;annual_energy_twh:number;peak_load_mw:number|null;uhv_branch_count:number}
export interface RegionalOverview {year:number;national:RegionalIndicators;provinces:Record<string,RegionalIndicators&{id:string;name:string;generator_locations:number}>;simulation:{installed_capacity_mw:number;maximum_load_mw:number;peak_timestamp:string;start:string;end:string;minimum_voltage_kv:number};coverage:{unassigned_locations:number;unassigned_capacity_mw:number;ambiguous_locations:number};definitions:Record<string,string>}
export type NetworkFilter = {mode:'auto'} | {mode:'manual';voltages:number[]};
export interface MapSettings {stage:Stage;layer:LayerId;resource:string;category:string;networkFilter:NetworkFilter;threeD:boolean;height:number;basemap:boolean}
export interface Prepared {features:GeoFeature[];summary:Summary;sourceCount:number;max:number;min:number;elapsed:number}
