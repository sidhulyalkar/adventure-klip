"""Pluggable multimodal scene descriptors: image embeddings, objects, and human pose.

No model downloads occur unless explicitly choosing an AI backend. Sparse frame sampling
limits cost. Object labels are proposals, not verified species or athlete identification.
"""
from __future__ import annotations
import math
from pathlib import Path
from typing import Protocol
from .media import require_tool
import subprocess

DEFAULT_QUERIES=['person','skier','skis','bicycle','mountain bike','tree','rock',
                 'snow','fish','shark','ray','sea turtle','coral','underwater reef']
VERSION='scene-sampling-pts-v3'


class SceneProvider(Protocol):
    name:str
    version:str
    def describe(self,image_bytes:bytes)->dict:...


class HistogramProvider:
    """Low-cost deterministic descriptor; NOT a semantic object detector."""
    name='histogram'
    version='1'
    def describe(self,image_bytes:bytes)->dict:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(image_bytes)) as image:
            image=image.convert('RGB').resize((48,48))
            hist=image.histogram()
            # R, G, B each have 256 bins -> 16 dimensions per channel.
            vector=[sum(hist[i*256+j:i*256+j+16])/2304 for i in range(3)
                    for j in range(0,256,16)]
        return {'embedding':vector,'objects':[],'keypoints':[],
                'notes':['Color histogram, not semantic understanding']}


class SiglipProvider:
    """Explicit opt-in semantic embedding through Hugging Face transformers."""
    name='siglip2'
    version='google/siglip2-base-patch16-224-v1'
    def __init__(self, model_id:str='google/siglip2-base-patch16-224'):
        import torch
        from transformers import AutoModel, AutoProcessor
        self.torch=torch
        self.processor=AutoProcessor.from_pretrained(model_id)
        self.model=AutoModel.from_pretrained(model_id)
        self.model.eval()
        if torch.backends.mps.is_available():
            self.device='mps'
        elif torch.cuda.is_available():
            self.device='cuda'
        else:
            self.device='cpu'
        self.model.to(self.device)
        self.version=model_id
        self.cache_config={'resolvedRevision':getattr(self.model.config,'_commit_hash',None),
                           'imageProcessor':self.processor.image_processor.to_dict()}
    def describe(self,image_bytes:bytes)->dict:
        from PIL import Image
        import io
        image=Image.open(io.BytesIO(image_bytes)).convert('RGB')
        inp=self.processor(images=image,return_tensors='pt').to(self.device)
        with self.torch.inference_mode():
            vector=self.model.get_image_features(**inp)
            if hasattr(vector,'pooler_output'):
                vector=vector.pooler_output
            vector=self.torch.nn.functional.normalize(vector,dim=-1)
        return {'embedding':vector.squeeze().cpu().float().tolist(),
                'objects':[],'keypoints':[], 'notes':['Image-text embedding, not object recognition']}


class OwlProvider:
    """Optional open-vocabulary detection on sampled frames; scores need validation."""
    name='owlv2'
    version='google/owlv2-base-patch16-ensemble'
    def __init__(self,threshold:float=0.2):
        from transformers import pipeline
        self.detector=pipeline('zero-shot-object-detection',model=self.version)
        self.threshold=threshold
        self.cache_config={'threshold':threshold,'queries':list(DEFAULT_QUERIES),
            'resolvedRevision':getattr(self.detector.model.config,'_commit_hash',None)}
    def describe(self,image_bytes:bytes)->dict:
        from PIL import Image
        import io
        image=Image.open(io.BytesIO(image_bytes)).convert('RGB')
        detections=self.detector(image,candidate_labels=DEFAULT_QUERIES)
        objects=[{'label':d['label'],'score':float(d['score']),
                  'bbox':dict(d['box']),'bboxFormat':'xyxy','coordinateSpace':'sampled_image_pixels','reviewed':False} for d in detections
                 if float(d['score'])>=self.threshold]
        return {'embedding':None,'objects':objects,'keypoints':[],
                'notes':['Zero-shot detection; labels are unverified']}


def frame_png(path:Path, t_ms:int, width:int=384, include_timing:bool=False):
    """Decode one PNG; optionally return the decoded source-PTS evidence.

    The historical bytes-only function remains compatible with callers and
    test fixtures. Semantic jobs request timing in the same decode pass.
    """
    from .frame_timing import extract_frame_png
    raw, timing = extract_frame_png(path, t_ms, width)
    return (raw, timing) if include_timing else raw


def describe_asset(path:Path,duration_ms:int,provider:SceneProvider,
                   *,sample_seconds:float=4.0,max_frames:int=300)->dict:
    if not math.isfinite(sample_seconds) or sample_seconds<=0 or max_frames<=0:
        raise ValueError('Sample interval and frame bound must be positive')
    frames=[]
    step=max(1,int(sample_seconds*1000),math.ceil(max(0,duration_ms)/max_frames))
    for t in range(0,max(0,duration_ms),step):
        if len(frames)>=max_frames:
            break
        from .semantic_jobs import frame_descriptor
        frames.append(frame_descriptor(provider,frame_png(path,t),t))
    return {'schemaVersion':1,'source':str(path),'provider':provider.name,
            'modelVersion':provider.version,'sampleIntervalMs':step,'frames':frames}


def generate_semantics(workspace:Path,*,backend:str='histogram',
                       sample_seconds:float=4.0,limit:int|None=None,pose_model:Path|None=None,
                       max_frames:int=300,asset_ids:set[str]|None=None)->dict:
    if not math.isfinite(sample_seconds) or sample_seconds<=0 or max_frames<=0:
        raise ValueError('Sample interval and frame bound must be positive and finite')
    if backend=='histogram':
        provider=HistogramProvider()
    elif backend=='siglip2':
        provider=SiglipProvider()
    elif backend=='owlv2':
        provider=OwlProvider()
    elif backend=='stack':
        stack=[SiglipProvider(),OwlProvider()]
        if pose_model is not None:
            stack.append(PoseProvider(pose_model))
        provider=SceneStack(stack)
    else:
        raise ValueError('Unknown backend; choose histogram, siglip2, owlv2, stack')
    from .semantic_jobs import run_semantics
    return run_semantics(workspace,provider,sample_seconds=sample_seconds,limit=limit,
                         max_frames=max_frames,asset_ids=asset_ids)

class PoseProvider:
    """Optional MediaPipe human pose. Useful only when a person's body is visible."""
    name='human-pose'
    version='mediapipe-pose-landmarker-v1'
    def __init__(self, model_path:Path):
        import mediapipe as mp
        if not model_path.is_file():
            raise ValueError('A locally downloaded .task human-pose model is required')
        from .ingest import sha_file
        self.cache_config={'modelSha256':sha_file(model_path),'mediapipeVersion':mp.__version__}
        self.mp=mp
        options=mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE)
        self.landmarker=mp.tasks.vision.PoseLandmarker.create_from_options(options)
    def describe(self,image_bytes:bytes)->dict:
        import numpy as np
        from PIL import Image
        import io
        array=np.asarray(Image.open(io.BytesIO(image_bytes)).convert('RGB'))
        img=self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=array)
        result=self.landmarker.detect(img)
        tracks=[]
        for person,points in enumerate(result.pose_landmarks):
            for idx,p in enumerate(points):
                tracks.append({'person':person,'jointIndex':idx,'x':p.x,'y':p.y,
                               'visibility':p.visibility,'coordinateSpace':'normalized_image','reviewed':False})
        return {'embedding':None,'objects':[], 'keypoints':tracks,
                'notes':['Human pose landmarks; not a first-person camera body pose']}


class SceneStack:
    """Combine independent descriptors without conflating their confidence scales."""
    name='stack'
    def __init__(self,providers:list[SceneProvider]):
        self.providers=providers
        self.cache_config={'providers':[{'name':p.name,'version':p.version,
            'config':getattr(p,'cache_config',{})} for p in providers]}
        self.version='+'.join(f'{p.name}:{p.version}' for p in providers)
    def describe(self,image_bytes:bytes)->dict:
        results=[p.describe(image_bytes) for p in self.providers]
        vectors=[r['embedding'] for r in results if r.get('embedding') is not None]
        return {'embedding':vectors[0] if vectors else None,
                'objects':[o for r in results for o in r.get('objects',[])],
                'keypoints':[k for r in results for k in r.get('keypoints',[])],
                'notes':[n for r in results for n in r.get('notes',[])]}
