"""Editorial preview only. Does not create Phase 7/10 publishing provenance."""
import hashlib, json, os, subprocess, sys
from pathlib import Path
if len(sys.argv)!=2:
    raise SystemExit('Usage: python tools/scroll-review.py /path/to/scroll-the-bible-media')
root=Path(sys.argv[1]).resolve()
repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo/'.github/scripts'))
from campaign_template import template_filters, verify_template_file, template_similarity
from PIL import Image
out=root/'review'
out.mkdir(exist_ok=True)
analysis=json.loads((root/'phase4b-37451496881/phase4-analysis/googledrive-17948870c17c52da.json').read_text())
source=root/'speaker.mov'
assert hashlib.sha256(source.read_bytes()).hexdigest()==analysis['sourceMedia']['contentSha256']
spec=json.loads((repo/'campaign-rules/281ed1b9-6d32-4c10-b46f-6becf467703d.json').read_text())['rules']['renderAssets']['template']
# Keep the complete source frame, including the app-store graphic and open Bible.
spec={**spec,'fit':'contain'}
art=root/'official-hook-01.png'
verify_template_file(art,spec)
rate=1.04
filters=template_filters(spec)
filters=[f.replace('[0:v]','[paced]') for f in filters]
filters.insert(0,f'[0:v]setpts=(PTS-STARTPTS)/{rate}[paced]')
filters.append(f'[0:a]asetpts=PTS-STARTPTS,atempo={rate}[audio]')
target=out/'scroll-the-bible-review.mp4'
cmd=['ffmpeg','-v','warning','-y','-i',str(source),'-loop','1','-i',str(art),'-filter_complex',';'.join(filters),'-map','[base]','-map','[audio]','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','2','-c:a','aac','-b:a','160k','-movflags','+faststart','-shortest',str(target)]
subprocess.run(cmd,check=True)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(target)]))
duration=float(probe['format']['duration'])
assert 59<duration<60
assert any(s['codec_type']=='audio' for s in probe['streams'])
subprocess.run(['ffmpeg','-v','error','-i',str(target),'-f','null','-'],check=True)
scores=[]
for ts in (3,15,30,57):
    frame=out/f'frame-{ts}.jpg'
    subprocess.run(['ffmpeg','-v','error','-y','-ss',str(ts),'-i',str(target),'-frames:v','1',str(frame)],check=True)
    scores.append(template_similarity(Image.open(art),Image.open(frame),spec))
assert min(scores)>.92
(out/'review-manifest.json').write_text(json.dumps({'status':'editorial_review_required','publishable':False,'campaignId':'281ed1b9-6d32-4c10-b46f-6becf467703d','source':analysis['sourceMedia'],'sourceDurationSeconds':analysis['durationSeconds'],'durationSeconds':duration,'playbackRate':rate,'entireSourceTimelineRetained':True,'providedTemplateSha256':spec['sha256'],'templateFit':'contain','templateSampleScores':scores,'videoSha256':hashlib.sha256(target.read_bytes()).hexdigest(),'modelTranscriptHumanVerified':False,'publishingProvenance':'none; standalone editorial preview, not a Phase 10 package'},indent=2))
(out/'post-copy-draft.txt').write_text("DRAFT — account/tag and disclosure review required before posting.\n\nTitle: Someone turned the entire Bible into a scrollable feed...\n\nCaption:\n@scrollthebibleapp\nScroll The Bible lets you swipe through scripture.\nDownload Scroll The Bible, available on the App Store & Play Store.\n\nRequired pinned comment:\nThis app is called Scroll The Bible, available on the App Store.\n",encoding='utf-8')
print('REVIEW_RENDER_PASS',duration,min(scores),str(target))
