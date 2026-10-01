const items=$input.all();
const source=$('Build Campaign Package').first().json||{};
const campaign=source.campaignPackage?.campaign||{};
const docs=items.map(i=>({url:i.json?.referenceUrl||null,type:i.json?.referenceType||'Unknown',data:String(i.json?.data||'')})).filter(x=>x.data);
const combined=docs.map(d=>'SOURCE_URL: '+d.url+'\nSOURCE_TYPE: '+d.type+'\n'+d.data).join('\n\n');
const stripHtml=s=>String(s||'').replace(/<[^>]+>/g,' ').replace(/&nbsp;/gi,' ').replace(/&amp;/gi,'&').replace(/&#39;/g,"'").replace(/&quot;/gi,'"');
const briefText=stripHtml(combined).replace(/\r\n/g,'\n').replace(/\r/g,'\n').replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
if(!briefText) throw new Error('Campaign reference materials are empty or inaccessible.');
const lines=briefText.split('\n').map(s=>s.trim()).filter(Boolean), lower=briefText.toLowerCase();
const refs=Array.isArray(campaign.referenceMaterials)?campaign.referenceMaterials:[];
const sourceDocumentUrl=refs[0]||campaign.url||null;
const mediaHints=[...new Set(refs.filter(u=>/app\.mediasilo\.com\/review\//i.test(String(u))).map(String))];
const driveHints=[...new Set(refs.filter(u=>/drive\.google\.com\/drive\//i.test(String(u))).map(String))];
const youtube=lower.includes('youtube'), tiktok=lower.includes('tiktok'), instagram=lower.includes('instagram');
const disclosureOptions=['#Ad','#Advertisement','#Sponsored'].filter(v=>lower.includes(v.toLowerCase()));
const durations=[...briefText.matchAll(/(?:minimum|at least|duration)[^\n]{0,80}?(\d+)\s*seconds?/gi)].map(m=>Number(m[1])).filter(Number.isFinite);
const rules={schemaVersion:'4.1',source:'campaign_reference_materials',sourceFormat:[...new Set(docs.map(d=>d.type))].join(','),campaignId:campaign.id||source.campaignId||null,campaignName:campaign.name||source.campaignPackage?.campaignName||null,sourceDocumentUrl,content:{officialFootageOnly:/official content|official footage|official source/i.test(briefText),externalFootageForbidden:/do not use|not use.*outside|outside the official/i.test(briefText),topic:{campaignTopic:campaign.name||null}},platforms:{youtubeShorts:{required:youtube},tiktok:{required:tiktok},instagram:{required:instagram}},branding:{logoRequired:/logo requirement|watermark.*logo|official.*logo/i.test(briefText),logoMustRemainVisible:/logo.*entire video|watermark.*entire/i.test(briefText)},disclosure:{required:/ftc disclosure|#ad|#advertisement|#sponsored/i.test(briefText),options:disclosureOptions,selected:disclosureOptions[0]||null},onScreenText:{required:/on-screen|onscreen/i.test(briefText),requiredLines:lines.filter(l=>/on-screen|onscreen|watermark|logo requirement/i.test(l)).slice(0,10)},video:{minimumDurationSeconds:durations.length?Math.min(...durations):null,highQualityOnly:/high-quality|high quality/i.test(briefText)},audio:{backgroundMusicAllowed:/background music.*allowed/i.test(briefText),originalAudioMustRemainAudible:/original.*audio.*audible/i.test(briefText)},originality:{originalEditRequired:/original edits|original edit/i.test(briefText),repostsForbidden:/repost|reposting|stealing other creators/i.test(briefText)},publishing:{liveDurationDays:/30 days minimum|30 days/i.test(briefText)?30:null,visibleLikesRequired:/likes.*visible/i.test(briefText),paidBoostingForbidden:/paid boosting|paid promotion|paid ads/i.test(briefText),duplicatePostingForbidden:/duplicate|same video more than/i.test(briefText)},assetSource:{officialContentFolderUrl:mediaHints[0]||driveHints[0]||null,sourceType:mediaHints.length?'MediaSilo':driveHints.length?'GoogleDrive':null,sourceUrl:mediaHints[0]||driveHints[0]||null},extraction:{extractedAt:new Date().toISOString(),characterCount:briefText.length,lineCount:lines.length,referenceCount:docs.length,referenceUrls:docs.map(d=>d.url)}};

rules.rawText=briefText;
rules.caption={mustGiveContext:/caption.*(?:mention|relevant|context)/i.test(briefText)};
rules.content.officialFootageOnly=rules.content.officialFootageOnly||/(?:own|provided|approved)[^\n]{0,50}footage|footage[^\n]{0,60}(?:provided|approved)/i.test(briefText);
rules.originality.originalEditRequired=rules.originality.originalEditRequired||/don't post raw rips|do not post raw|but edit it/i.test(briefText);
rules.publishing.paidBoostingForbidden=rules.publishing.paidBoostingForbidden||/no paying for boosts|no story boosting|view botting/i.test(briefText);
// Preserve instructions as evidence, but send only explicitly quoted display text to the renderer.
rules.onScreenText.instructionEvidence=lines.filter(l=>/on-screen|onscreen|text overlays/i.test(l));
rules.onScreenText.requiredLines=[...new Set(rules.onScreenText.instructionEvidence.flatMap(l=>
  [...l.matchAll(/[“"]([^”"\n]{1,120})[”"]/g)].map(m=>m[1].trim())
).filter(Boolean))];
const globalTagLines=lines.filter(l=>/tag\s+@[a-z0-9_.]+/i.test(l)&&/(?:every post|every platform|in the caption on every)/i.test(l));
const globalTags=[...new Set(globalTagLines.flatMap(l=>[...l.matchAll(/tag\s+(@[a-z0-9_.]+)/gi)].map(m=>m[1])))];
for(const [key,label] of [['youtubeShorts','youtube'],['instagram','instagram'],['tiktok','tiktok']]){
  const explicit=lines.filter(l=>l.toLowerCase().includes(label)&&/tag\s+@/i.test(l)&&!/optional|not required|feel free/i.test(l));
  const tags=[...new Set(explicit.flatMap(l=>[...l.matchAll(/tag\s+(@[a-z0-9_.]+)/gi)].map(m=>m[1])))];
  const choices=tags.length?tags:globalTags;
  rules.platforms[key].accountTag=choices.length===1?choices[0]:null;
}

const evidence=[{rule:'officialContentFolderUrl',value:rules.assetSource.sourceUrl,evidence:rules.assetSource.sourceUrl||'Discovered in fetched references',source:'campaign_reference_materials'},{rule:'youtubeShorts',value:youtube,evidence:lines.find(l=>/youtube/i.test(l))||'Campaign page references YouTube',source:'campaign_reference_materials'},{rule:'disclosure',value:rules.disclosure.selected||rules.disclosure.required,evidence:lines.find(l=>/ftc disclosure|#ad|#advertisement|#sponsored/i.test(l))||'No explicit disclosure token found',source:'campaign_reference_materials'},{rule:'videoDuration',value:rules.video.minimumDurationSeconds,evidence:lines.find(l=>/seconds/i.test(l))||'No explicit minimum duration found',source:'campaign_reference_materials'},{rule:'branding',value:rules.branding.logoRequired,evidence:lines.find(l=>/logo|watermark/i.test(l))||'No explicit logo rule found',source:'campaign_reference_materials'},{rule:'originality',value:rules.originality.repostsForbidden,evidence:lines.find(l=>/repost|original/i.test(l))||'No explicit repost rule found',source:'campaign_reference_materials'},{rule:'sourceType',value:rules.assetSource.sourceType,evidence:rules.assetSource.sourceUrl||'Source not found in campaign references',source:'campaign_reference_materials'}];
return[{json:{...source,campaignReferenceMaterials:docs,campaignBrief:{rawText:briefText,rawCombined:combined,parsedRules:rules,ruleEvidence:evidence,assetSources:[...mediaHints,...driveHints],extraction:rules.extraction}}}];
