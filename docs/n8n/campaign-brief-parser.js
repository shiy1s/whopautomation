// Existing Parse Campaign Brief Code node: aggregate fetched references only.
// Extract explicit instructions and preserve their evidence; never invent copy.
const items = $input.all();
const source = $('Build Campaign Package').first().json || {};
const campaign = source.campaignPackage?.campaign || {};
let referenceInputs=[];
try { referenceInputs=$('Prepare Campaign References').all(); } catch {}
const docs = items.map((i,index) => ({
  url: i.json?.referenceUrl || referenceInputs[index]?.json?.referenceUrl || null,
  type: i.json?.referenceType || referenceInputs[index]?.json?.referenceType || 'Unknown',
  data: String(i.json?.data || ''),
})).filter(x => x.data.trim());
const combined = docs.map(d => 'SOURCE_URL: ' + d.url + '\nSOURCE_TYPE: ' + d.type + '\n' + d.data).join('\n\n');
const stripHtml = s => String(s || '')
  .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, ' ')
  .replace(/<\/?(?:p|div|li|h[1-6]|tr)\b[^>]*>|<br\s*\/?>/gi, '\n')
  .replace(/<[^>]+>/g, ' ')
  .replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&')
  .replace(/&#39;|&apos;/gi, "'").replace(/&quot;/gi, '"');
const clean = s => stripHtml(s).replace(/\r\n?/g, '\n').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
if (!docs.some(d => clean(d.data))) throw new Error('Campaign reference materials are empty or inaccessible.');
const briefText = clean(combined);
const records = docs.flatMap(d => clean(d.data).split('\n').map(text => ({text: text.trim(), sourceUrl: d.url})).filter(r => r.text));
const lines = records.map(r => r.text);
const unique = values => [...new Set(values)];
const uniqueCase = values => [...new Map(values.map(v => [v.toLowerCase(), v])).values()];
const refs = Array.isArray(campaign.referenceMaterials) ? campaign.referenceMaterials : [];
const sourceDocumentUrl = refs[0] || campaign.url || null;
const mediaHints = unique(refs.filter(u => /app\.mediasilo\.com\/review\//i.test(String(u))).map(String));
const driveHints = unique(refs.filter(u => /drive\.google\.com\/drive\//i.test(String(u))).map(String));
const optional = /\b(?:optional|not required|not mandatory|feel free|for example|e\.g\.)\b/i;
const prohibited = /\b(?:do not|don't|don’t|must not|never|avoid|forbidden|prohibited|not allowed|not permitted|not eligible|no)\b/i;
const unresolved = [];

// A reference to source footage is not permission to publish on that service.
const platformDefinitions = [
  ['youtubeShorts', /\byoutube(?:\s+shorts)?\b/i],
  ['instagram', /\binstagram(?:\s+reels)?\b/i],
  ['tiktok', /\btik\s*tok\b/i],
];
const platforms = Object.fromEntries(platformDefinitions.map(([key]) => [key, {
  required: false, allowed: null, forbidden: false, accountTag: null, instructionEvidence: [],
}]));
let closedPlatformList = false;
for (let index = 0; index < lines.length; index++) {
  const line = lines[index];
  const heading = /^(?:approved|allowed|eligible|supported)\s+(?:publishing\s+)?platforms?\s*:?$/i.test(line);
  const clauses = (heading ? lines[index + 1] || '' : line).split(/;|\.(?=\s|$)|,(?=\s*(?:do not|don't|no |never|use |post ))/i);
  for (const clause of clauses) {
    const found = platformDefinitions.filter(([, pattern]) => pattern.test(clause));
    if (!found.length) continue;
    const forbidden = prohibited.test(clause) && !/\bnot (?:required|mandatory)\b/i.test(clause);
    const namedList = clause.replace(/youtube(?:\s+shorts)?|instagram(?:\s+reels)?|tik\s*tok|\b(?:and|or|only|x|tag|platforms?)\b/gi, '').replace(/[^a-z]/gi, '') === '';
    const approval = heading || namedList || /\b(?:approved|allowed|eligible|supported)\s+(?:publishing\s+)?platforms?\s*:/i.test(clause) || /\b(?:post|publish|posting|publishing|submit|submission)\b[^.]{0,100}\b(?:to|on|platforms?)\b/i.test(clause);
    if (approval && (/\bonly\b/i.test(clause) || heading || /\b(?:approved|allowed|eligible)\s+(?:publishing\s+)?platforms?\s*:/i.test(clause))) closedPlatformList = true;
    for (const [key] of found) {
      if (forbidden || approval) platforms[key].instructionEvidence.push(line);
      if (forbidden) platforms[key].forbidden = true;
      if (approval && !forbidden) platforms[key].allowed = true;
    }
  }
}
for (const p of Object.values(platforms)) {
  if (p.forbidden || (closedPlatformList && p.allowed !== true)) p.allowed = false;
  p.required = p.allowed === true; // Preserve the legacy handoff field.
  p.instructionEvidence = unique(p.instructionEvidence);
}
if (!Object.values(platforms).some(p => p.allowed === true)) unresolved.push('No supported publishing platform has explicit approval in the fetched brief.');

// Clip bounds are separate from hook timing, budgets and post-retention periods.
const minima = [], maxima = [], durationEvidence = [];
const number = '(\\d+(?:\\.\\d+)?)';
const unit = '(seconds?|secs?|s|minutes?|mins?|m)';
const seconds = (value, suffix) => Number(value) * (/^m/i.test(suffix || '') ? 60 : 1);
for (const line of lines) {
  if (!/\b(?:clips?|videos?|duration|length)\b/i.test(line) && !/^\s*[-*]?\s*(?:minimum|maximum|min\b|max\b|at least|at most)\b/i.test(line)) continue;
  let matched = false;
  const range = new RegExp('(?:between\\s+)?' + number + '\\s*' + unit + '?\\s*(?:[-–—]|to|and)\\s*' + number + '\\s*' + unit + '\\b', 'gi');
  for (const m of line.matchAll(range)) {
    minima.push(seconds(m[1], m[2] || m[4])); maxima.push(seconds(m[3], m[4])); matched = true;
  }
  for (const [pattern, destination, strict] of [
    ['(?:minimum|min|at least|no less than|not less than)\\b[^\\d\\n]{0,45}', minima, 0],
    ['(?:maximum|max|at most|no more than|not more than|no longer than|not longer than|up to)\\b[^\\d\\n]{0,45}', maxima, 0],
    ['(?:under|less than|shorter than)\\s+', maxima, -0.001],
    ['(?:over|more than|longer than)\\s+', minima, 0.001],
  ]) {
    for (const m of line.matchAll(new RegExp('\\b' + pattern + number + '\\s*' + unit + '\\b', 'gi'))) {
      // "No more/less than" already supplied an inclusive bound above.
      if (strict && /(?:no|not)\s+$/i.test(line.slice(0, m.index))) continue;
      destination.push(Number((seconds(m[1], m[2]) + strict).toFixed(3))); matched = true;
    }
  }
  for (const [suffix, destination] of [['(?:minimum|min|or longer|or more)', minima], ['(?:maximum|max|or shorter|or less)', maxima]]) {
    for (const m of line.matchAll(new RegExp(number + '\\s*' + unit + '\\s*' + suffix + '\\b', 'gi'))) {
      destination.push(seconds(m[1], m[2])); matched = true;
    }
  }
  if (!matched) {
    const exact = line.match(new RegExp('^\\s*(?:clip|video)?\\s*(?:duration|length)\\s*[:=]\\s*' + number + '\\s*' + unit + '\\s*[.!]?$', 'i'));
    if (exact) { minima.push(seconds(exact[1], exact[2])); maxima.push(seconds(exact[1], exact[2])); matched = true; }
  }
  if (matched) durationEvidence.push(line);
}
const minimumDurationSeconds = minima.length ? Math.max(...minima) : null;
const maximumDurationSeconds = maxima.length ? Math.min(...maxima) : null;
if ((minimumDurationSeconds !== null && minimumDurationSeconds <= 0) || (maximumDurationSeconds !== null && maximumDurationSeconds <= 0) || (minimumDurationSeconds !== null && maximumDurationSeconds !== null && minimumDurationSeconds > maximumDurationSeconds)) {
  throw new Error('CONTRADICTORY_DURATION_BOUNDS: review the campaign clip-duration instructions.');
}

const captionEvidence = lines.filter(l => /caption|description|hashtag/i.test(l));
const mustGiveContext = captionEvidence.some(l => /(?:caption|description)[^\n]{0,140}(?:mention|relevant|context)|(?:mention|context)[^\n]{0,100}(?:caption|description)/i.test(l) && !/\b(?:no|not) context (?:is )?required\b/i.test(l));
// Copy only a labelled fact or a declarative sentence about this campaign's
// subject. Instructions such as "mention the product" are not usable context.
const campaignName = String(campaign.name || source.campaignPackage?.campaignName || '');
const normalizedName = campaignName.toLowerCase().replace(/[^a-z0-9]+/g, ' ');
const contextCandidates = [];
for (const record of records) {
  const line = record.text.replace(/^[-*]\s*/, '');
  const labelled = line.match(/^(?:caption context|context text|brand description|product description)\s*:\s*(.+)$/i);
  const fact = line.match(/^([a-z0-9][a-z0-9 &'’-]{1,65}?)\s+(is|makes|builds|provides|produces|offers)\s*:?\s+(.+)$/i);
  let text = null;
  if (labelled) text = labelled[1].trim();
  else if (fact && normalizedName.includes(fact[1].toLowerCase().replace(/[^a-z0-9]+/g, ' '))) text = fact[1] + ' ' + fact[2] + ' ' + fact[3];
  if (!text || text.length > 600 || /^(?:please |you |must |mention |include |write |use |add |don't |do not )/i.test(text) || /https?:\/\//i.test(text)) continue;
  contextCandidates.push({text, evidence: record.text, sourceUrl: record.sourceUrl});
}
const context = contextCandidates[0] || null;
if (mustGiveContext && !context) unresolved.push('Caption context is required but no explicit factual context was found; review the brief before Phase 9.');

const requiredHashtags = [], forbiddenHashtags = [], disclosureOptions = [];
let disclosureRequired = false, disclosurePlacement = null;
for (const line of lines) {
  const tags = [...line.matchAll(/#[a-z0-9_]{1,50}\b/gi)].map(m => m[0]);
  const forbidden = prohibited.test(line) && !/\bnot (?:required|mandatory)\b/i.test(line);
  const disclosureTags = tags.filter(t => /^#(?:ad|advertisement|sponsored)$/i.test(t));
  if (forbidden) forbiddenHashtags.push(...tags);
  else if (!optional.test(line) && /\b(?:include|use|add|required|must|hashtags?|disclosure)\b/i.test(line)) requiredHashtags.push(...tags.filter(t => !disclosureTags.includes(t)));
  if (!forbidden && !optional.test(line) && (disclosureTags.length || /\b(?:ftc|sponsorship|advertising) disclosure\b/i.test(line))) {
    disclosureRequired = true;
    disclosureOptions.push(...disclosureTags);
    if (/first (?:separate )?line|own line (?:at the top|first)|start of (?:the )?(?:caption|description)/i.test(line)) disclosurePlacement = 'first_separate_line';
    else if (/first hashtag after (?:the )?(?:text|caption|description)/i.test(line)) disclosurePlacement = 'first_hashtag_after_text';
  }
}
const bannedTags = uniqueCase(forbiddenHashtags);
const chosenTags = uniqueCase(requiredHashtags).filter(t => !bannedTags.some(b => b.toLowerCase() === t.toLowerCase()));
const disclosureChoices = uniqueCase(disclosureOptions).filter(t => !bannedTags.some(b => b.toLowerCase() === t.toLowerCase()));
if (disclosureRequired && !disclosureChoices.length) unresolved.push('Disclosure is required but an approved disclosure token must be reviewed.');

const onScreenEvidence = lines.filter(l => /on-screen|onscreen|text overlays/i.test(l));
const requiredLines = unique(onScreenEvidence.filter(l => !prohibited.test(l) && !optional.test(l)).flatMap(l => [...l.matchAll(/[“"]([^”"\n]{1,120})[”"]/g)].map(m => m[1].trim())).filter(Boolean));
const logoEvidence = lines.filter(l => /logo|watermark/i.test(l));
const rules = {
  schemaVersion: '4.2', source: 'campaign_reference_materials', sourceFormat: unique(docs.map(d => d.type)).join(','),
  campaignId: campaign.id || source.campaignId || null, campaignName: campaignName || null, sourceDocumentUrl, rawText: briefText,
  content: {
    creatorRequirements: campaign.requirements?.creatorRequirements || [],
    officialFootageOnly: /official content|official footage|official source|(?:own|provided|approved)[^\n]{0,50}footage|footage[^\n]{0,60}(?:provided|approved)/i.test(briefText),
    externalFootageForbidden: /(?:do not|don't|no|forbid)[^\n]{0,45}(?:outside|external|unapproved|third.party) (?:footage|content)|outside the official/i.test(briefText),
    topic: {campaignTopic: campaign.name || null},
  },
  platforms,
  branding: {
    logoRequired: logoEvidence.some(l => !prohibited.test(l) && !optional.test(l) && /logo requirement|(?:include|use|add|required|must)[^\n]{0,45}(?:logo|watermark)|(?:logo|watermark)[^\n]{0,45}(?:required|must)/i.test(l)),
    logoMustRemainVisible: logoEvidence.some(l => !prohibited.test(l) && /(?:logo|watermark)[^\n]{0,45}(?:entire video|throughout|always visible)/i.test(l)),
    instructionEvidence: logoEvidence,
  },
  caption: {mustGiveContext, contextText: context?.text || null, contextEvidence: context, requiredHashtags: chosenTags, forbiddenHashtags: bannedTags, instructionEvidence: captionEvidence,
    captionsRequired: lines.some(l => /(?:use|must|require|include)[^.\n]{0,40}(?:bold |timed |subtitles|captions)/i.test(l) && !prohibited.test(l)),
    hookRequired: lines.some(l => /(?:strong hook|hook them|hook required|required hook)/i.test(l) && !prohibited.test(l))},
  disclosure: {required: disclosureRequired, options: disclosureChoices, selected: disclosureChoices[0] || null, placement: disclosurePlacement},
  onScreenText: {required: onScreenEvidence.some(l => !prohibited.test(l) && !optional.test(l)), requiredLines, instructionEvidence: onScreenEvidence},
  video: {minimumDurationSeconds, maximumDurationSeconds, durationEvidence, highQualityOnly: /high-quality|high quality/i.test(briefText)},
  audio: {backgroundMusicAllowed: lines.some(l => /background music.*allowed/i.test(l) && !prohibited.test(l)), originalAudioMustRemainAudible: /original.*audio.*audible/i.test(briefText)},
  originality: {originalEditRequired: /original edits|original edit|don't post raw rips|do not post raw|but edit it/i.test(briefText), repostsForbidden: /repost|reposting|stealing other creators/i.test(briefText)},
  publishing: {liveDurationDays: /30 days minimum|30 days/i.test(briefText) ? 30 : null, visibleLikesRequired: /likes.*visible/i.test(briefText), paidBoostingForbidden: /paid boosting|paid promotion|paid ads|no paying for boosts|no story boosting|view botting/i.test(briefText), duplicatePostingForbidden: /duplicate|same video more than/i.test(briefText)},
  assetSource: {officialContentFolderUrl: mediaHints[0] || driveHints[0] || null, sourceType: mediaHints.length ? 'MediaSilo' : driveHints.length ? 'GoogleDrive' : null, sourceUrl: mediaHints[0] || driveHints[0] || null},
  extraction: {extractedAt: new Date().toISOString(), characterCount: briefText.length, lineCount: lines.length, referenceCount: docs.length, referenceUrls: docs.map(d => d.url), unresolvedRequirements: unresolved},
};
const globalTagLines = lines.filter(l => /tag\s+@[a-z0-9_.]+/i.test(l) && /(?:every post|every platform|in the caption on every)/i.test(l) && !optional.test(l) && !prohibited.test(l));
const globalTags = uniqueCase(globalTagLines.flatMap(l => [...l.matchAll(/tag\s+(@[a-z0-9_.]+)/gi)].map(m => m[1])));
for (const [key, pattern] of platformDefinitions) {
  const explicit = lines.filter(l => pattern.test(l) && /tag\s+@/i.test(l) && !optional.test(l) && !prohibited.test(l));
  const tags = uniqueCase(explicit.flatMap(l => [...l.matchAll(/tag\s+(@[a-z0-9_.]+)/gi)].map(m => m[1])));
  const choices = tags.length ? tags : globalTags;
  rules.platforms[key].accountTag = choices.length === 1 ? choices[0] : null;
  if (choices.length > 1) unresolved.push('Conflicting required account tags for ' + key + '; review attribution before publishing.');
}
const evidence = [
  {rule: 'officialContentFolderUrl', value: rules.assetSource.sourceUrl, evidence: rules.assetSource.sourceUrl || 'No supported folder URL found in campaign references'},
  {rule: 'youtubeShorts', value: platforms.youtubeShorts.allowed, evidence: platforms.youtubeShorts.instructionEvidence.join('\n') || 'No explicit publishing permission found'},
  {rule: 'captionContext', value: rules.caption.contextText, evidence: context?.evidence || 'No explicit factual context found', sourceUrl: context?.sourceUrl || null},
  {rule: 'disclosure', value: rules.disclosure.selected || rules.disclosure.required, evidence: lines.filter(l => /disclosure|#(?:ad|advertisement|sponsored)\b/i.test(l)).join('\n') || 'No explicit disclosure rule found'},
  {rule: 'videoDuration', value: {minimumDurationSeconds, maximumDurationSeconds}, evidence: durationEvidence.join('\n') || 'No explicit clip-duration bounds found'},
  {rule: 'branding', value: rules.branding.logoRequired, evidence: logoEvidence.join('\n') || 'No explicit logo rule found'},
  {rule: 'originality', value: rules.originality.repostsForbidden, evidence: lines.filter(l => /repost|original|raw rips/i.test(l)).join('\n') || 'No explicit repost rule found'},
  {rule: 'sourceType', value: rules.assetSource.sourceType, evidence: rules.assetSource.sourceUrl || 'Source not found in campaign references'},
].map(e => ({...e, source: 'campaign_reference_materials'}));
return [{json: {...source, campaignReferenceMaterials: docs, campaignBrief: {rawText: briefText, rawCombined: combined, parsedRules: rules, ruleEvidence: evidence, assetSources: [...mediaHints, ...driveHints], extraction: rules.extraction}}}];
