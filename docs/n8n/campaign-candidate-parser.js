// Read public page data as JSON; never execute page scripts or infer budget from layout.
const html=String($json?.data||'');
if(!html)throw new Error('Campaign detail page returned no HTML.');
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const idFromUrl=value=>String(value||'').match(/\/discover\/([0-9a-f-]{36})(?:[/?#]|$)/i)?.[1]?.toLowerCase();
let expected=String($json.campaignId||'').toLowerCase()||idFromUrl($json.campaignUrl);
if(!expected){try{expected=String($('Extract Campaign URLs').item.json.campaignId||'').toLowerCase();}catch{}}
const products=[];
for(const m of html.matchAll(/<script\b[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)){
  try{const d=JSON.parse(m[1]);for(const p of (Array.isArray(d)?d:[d]))if(p['@type']==='Product')products.push(p);}catch{}
}
const product=products.find(p=>!expected||idFromUrl(p.url)===expected);
expected=expected||idFromUrl(product?.url);
if(!uuid.test(expected||''))throw new Error('CAMPAIGN_PAGE_IDENTITY_MISSING');
if(products.length&&!product)throw new Error('CAMPAIGN_PAGE_IDENTITY_MISMATCH');
function readObject(raw){
  if(raw[0]!=='{')return null;
  let depth=0,quoted=false,escaped=false;
  for(let i=0;i<raw.length;i++){
    const ch=raw[i];
    if(quoted){if(escaped)escaped=false;else if(ch==='\\')escaped=true;else if(ch==='"')quoted=false;}
    else if(ch==='"')quoted=true;
    else if(ch==='{')depth++;
    else if(ch==='}'&&--depth===0){try{return JSON.parse(raw.slice(0,i+1));}catch{return null;}}
  }
  return null;
}
const cards=[];
for(const m of html.matchAll(/self\.__next_f\.push\((\[.*?\])\)/g)){
  let chunk;try{chunk=JSON.parse(m[1])[1];}catch{continue;}
  if(typeof chunk!=='string')continue;
  for(const c of chunk.matchAll(/"card"\s*:\s*/g)){
    const card=readObject(chunk.slice(c.index+c[0].length));
    if(card&&String(card.id).toLowerCase()===expected)cards.push(card);
  }
}
const card=cards[0];
const skip=reason=>({json:{campaign:null,skip:true,skipReason:reason,campaignId:expected}});
if(!card)return skip('verified_campaign_data_missing');
if(cards.some(c=>c.budgetCents!==card.budgetCents||c.status!==card.status||c.metrics?.budgetSpentCents!==card.metrics?.budgetSpentCents))throw new Error('CAMPAIGN_PAGE_CONFLICTING_DATA');
const total=card.budgetCents,spent=card.metrics?.budgetSpentCents;
if(!Number.isSafeInteger(total)||!Number.isSafeInteger(spent)||total<0||spent<0||spent>total)return skip('campaign_budget_invalid');
const labels={youtube:'YouTube',instagram:'Instagram',tiktok:'TikTok',facebook:'Facebook',x:'X'};
const payouts=(Array.isArray(card.payouts)?card.payouts:[]).filter(p=>labels[p.platform]&&p.payoutType==='cpm'&&Number.isSafeInteger(p.rateCents)&&p.rateCents>0);
const platforms=[...new Set(payouts.map(p=>labels[p.platform]))];
if(!card.name||!platforms.length)return skip('campaign_payouts_missing');
// Explicit campaign references may point to ANY supported adapter, not site navigation.
const refs=[...new Set((Array.isArray(card.referenceMaterials)?card.referenceMaterials:[])
  .map(x=>typeof x==='string'?x:x?.url).filter(x=>typeof x==='string')
  .map(x=>x.trim()).filter(x=>/^https?:\/\/[^\s\\<>"']+$/i.test(x)))];
const requirements=Array.isArray(card.contentRequirements?.items)?card.contentRequirements.items.filter(x=>typeof x==='string'):[];
const description=String(card.description||product?.description||'');
const detail=[description,...requirements].join('\n');
return {json:{campaign:{id:expected,name:String(card.name),description,url:'https://contentrewards.com/discover/'+expected,
  status:String(card.status||'').toLowerCase(),private:card.private===true,requiresApplication:card.requiresApplication===true,
  organization:{id:card.organizationId||null,name:card.organizationName||null,experienceId:card.organizationExperienceId||null,verified:card.organizationVerified===true},
  budget:{totalCents:total,spentCents:spent,remainingCents:total-spent,totalUSD:total/100,remainingUSD:(total-spent)/100},
  payout:{type:'cpm',platforms,byPlatform:payouts.map(p=>({...p,platform:labels[p.platform],ratePer1K:p.rateCents/100}))},
  metrics:{creatorCount:card.metrics?.creatorCount??null,approvedSubmissionCount:card.metrics?.approvedSubmissionCount??null,totalViews:card.metrics?.totalViews??null},
  referenceMaterials:refs,
  requirements:{approvalRequired:/approval required|requires approval|pre-approval/i.test(detail),applicationRequired:card.requiresApplication===true,
    paidPromotionForbidden:/paid promotion|paid boosting|paid ads/i.test(detail),noRepost:/no repost|reposts forbidden/i.test(detail),minimumSeconds:null,creatorRequirements:requirements},
  detailText:detail,provenance:{source:'public_campaign_card',url:'https://contentrewards.com/discover/'+expected,updatedAt:card.updatedAt||null}},fetchedAt:new Date().toISOString()}};
