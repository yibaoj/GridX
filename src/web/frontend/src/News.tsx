import {useState} from 'react';
import {ArrowUpRight,Newspaper,Search,X} from 'lucide-react';
import './news.css';

const categories=['全部','电网建设','新能源','科研动态','站内消息'] as const;
type Category=typeof categories[number];
type NewsArticle={
  id:string;
  category:Exclude<Category,'全部'>;
  title:string;
  summary?:string;
  publishedAt?:string;
  source?:string;
  sourceUrl?:string;
};

// Add verified articles here when publication dates and original sources are available.
const articles:NewsArticle[]=[];

export default function News() {
  const [category,setCategory]=useState<Category>('全部');
  const [query,setQuery]=useState('');
  const keyword=query.trim().toLocaleLowerCase();
  const filtered=articles.filter(article=>
    (category==='全部'||article.category===category)&&
    [article.title,article.summary,article.source].some(value=>value?.toLocaleLowerCase().includes(keyword))
  );
  const hasFilters=category!=='全部'||keyword!=='';
  const reset=()=>{setCategory('全部');setQuery('');};

  return <section className="news" aria-label="电力与能源资讯">
    <p className="news-intro">关注电网建设、新能源发展与电力科研进展。</p>
    <div className="panel news-panel">
      <div className="news-toolbar">
        <div className="news-categories" role="group" aria-label="新闻分类">
          {categories.map(item=><button type="button" key={item} aria-pressed={category===item}
            onClick={()=>setCategory(item)}>{item}</button>)}
        </div>
        <div className="news-search">
          <Search size={17} aria-hidden="true"/>
          <input type="search" aria-label="搜索新闻" placeholder="搜索标题或关键词"
            value={query} onChange={event=>setQuery(event.target.value)}/>
          {query&&<button className="icon-button" type="button" aria-label="清除新闻搜索" onClick={()=>setQuery('')}><X size={15}/></button>}
        </div>
      </div>
      <div className="news-list-heading"><h2>{category==='全部'?'全部消息':category}</h2>
        <span role="status" aria-live="polite">共 {filtered.length} 条</span>
      </div>
      {filtered.length>0?<div className="news-list">{filtered.map(article=>
        <article className="news-article" key={article.id}>
          <div className="news-article-meta"><span>{article.category}</span>
            {article.publishedAt&&<time dateTime={article.publishedAt}>{article.publishedAt}</time>}</div>
          <h3>{article.sourceUrl?<a href={article.sourceUrl} target="_blank" rel="noreferrer">{article.title}<ArrowUpRight size={16}/></a>:article.title}</h3>
          {article.summary&&<p>{article.summary}</p>}
          {article.source&&<span className="news-source">来源 · {article.source}</span>}
        </article>
      )}</div>:<div className="news-empty">
        <span className="news-empty-icon"><Newspaper size={30} strokeWidth={1.4}/></span>
        <h3>{hasFilters?'暂无符合条件的消息':'新闻内容整理中'}</h3>
        <p>{keyword?'请尝试其他关键词，或查看全部消息。':category!=='全部'?`「${category}」消息将在整理后发布。`:'电网、新能源与科研相关消息将在这里发布。'}</p>
        {hasFilters&&<button type="button" className="button" onClick={reset}>查看全部消息</button>}
      </div>}
      <div className="news-list-footer">消息发布后将展示日期与原文来源。</div>
    </div>
  </section>;
}
