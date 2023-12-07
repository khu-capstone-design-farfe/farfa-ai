from fastapi import FastAPI, File, UploadFile, Form, Request
from classes import *
from setting import *
from pyannote.audio import Pipeline
import numpy as np
import torch
from speechbrain.pretrained import SepformerSeparation as separator
import diariazation
import soundfile as sf
import os
import httpx
import asyncio
from pydub import AudioSegment

def preprocess_audio(path):
    ext = path.split(".")[-1]
    sound = AudioSegment.from_file(path, format=ext)
    sound = sound.set_channels(1)
    dst = ".".join(path.split(".")[:-1])+".wav"
    sound = sound.set_frame_rate(16000)
    sound.export(dst, format="wav")
    return path

pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.0",
  use_auth_token=tk)
pipeline.to(torch.device("cuda"))
if num_speaker==2:
    separation_model = separator.from_hparams(source="speechbrain/sepformer-whamr", savedir='pretrained_models/sepformer-whamr,run_opts={"device":"cuda"}')
if num_speaker==3:
    separation_model = separator.from_hparams(source="speechbrain/sepformer-wsj03mix", savedir='pretrained_models/sepformer-wsj03mix',run_opts={"device":"cuda"})
enh_model = separator.from_hparams(source="speechbrain/sepformer-wham16k-enhancement", savedir='pretrained_models/sepformer-wham16k-enhancement',run_opts={"device":"cuda"})

app = FastAPI()

async def request(client, URI, upload=None, obj=None, header=None,json=None):
    resp = await client.post(URI, files=upload, json=json, data=obj, timeout=None, headers=header)
    cat = resp.json()
    return cat

async def progress_request(URI, fileName, user, seq):
    async with httpx.AsyncClient() as client:
        result = await request(client, URI, json={"fileName": fileName, "user" : user, "seq" : seq})
    return result

@app.get("/")
#async def root():
async def root(request:Request):
    return {"message" : "hello world!"}

@app.post("/api/record")
async def records(req:Request, fileName:str=Form(), user:str=Form(),
                  speakerNum:int=Form(), file: UploadFile=File()):
    global UPLOAD_DIRECTORY
    if BE_URI != None:
        be_uri = BE_URI
    else:
        be_uri = f"http://{req.client.host}:{req.client.port}/api/progress"
    try:
        progress_0 = await progress_request(be_uri, fileName, user, 0) 
        print(be_uri, "200 OK")
    except:
        print(be_uri, "ERR")
    file_extension = fileName.split('.')[-1]
    contents = await file.read()
    new_filename = user + '_recordfile.' + file_extension
    file_path = os.path.join(UPLOAD_DIRECTORY, new_filename)
    with open(file_path, "wb") as fp:
        fp.write(contents)
    ext = file_path.split(".")[-1]
    sound = AudioSegment.from_file(file_path, format=ext)
    sound_len = int(len(sound) // 1000)
    sound = sound.set_channels(1)
    file_path = ".".join(file_path.split(".")[:-1])+".wav"
    sound = sound.set_frame_rate(16000)
    sound.export(file_path, format="wav")
    print("file preprocess done for", file_path)
    
    try:
        progress_1 = await progress_request(be_uri, fileName, user, 1)
        print(be_uri, "200 OK")
    except:
        print(be_uri, "ERR")
    fileinfo = VoiceFile(user, speakerNum, file_path)
    #diar_result = aa # !////
    diar_result = diariazation.split_audios(fileinfo, pipeline, separation_model, enh_model)
    print("diarization done")

    try:
        progress_2 = await progress_request(be_uri, fileName, user, 2)
        print(be_uri, "200 OK")
    except:
        print(be_uri, "ERR")
    tempfilename = os.path.join(TEMP_DIRECTORY,file_path.split('/')[-1].split('.')[0]+'_temp')
    async with httpx.AsyncClient() as client:
        tasks = []
        for i in diar_result:
            sf.write(tempfilename+str(i.seq)+".wav", i.audio, 16000, format="WAV")
            with open(tempfilename+str(i.seq)+".wav", 'rb') as fp:
                ct = fp.read()
            # ct = contents #!!!
            tasks.append(request(client, ASR_URIS[i.seq%3], upload={'file':ct}, obj={"seq" : i.seq, "user" : user}))
        result = await asyncio.gather(*tasks)
    for i in diar_result:
        os.remove(tempfilename+str(i.seq)+".wav")
    for i in result:
        diar_result[i['seq']].message = i['message']


    # clova sentiment
    try:
        progress_3 = await progress_request(be_uri, fileName, user, 3)
        print(be_uri, "200 OK")
    except:
        print(be_uri, "ERR")
    sentences = [x.message for x in diar_result]
    async with httpx.AsyncClient() as client:
        tasks = [request(client, CLOVA_URI, json={'content':st},
                header = CLOVA_HEADERS) for st in sentences]
        result = await asyncio.gather(*tasks)
    part_all = np.array([0.0, 0.0, 0.0, 0])
    for ind, i in enumerate(result):
        try:
            data = i['document']['confidence']
            part_all += [data['positive'], data['negative'], data['neutral'], 1]
            diar_result[ind].positive = data['positive']
            diar_result[ind].negative = data['negative']
            diar_result[ind].neutral = data['neutral']
        except:
            diar_result[ind].positive = 0.0
            diar_result[ind].negative = 0.0
            diar_result[ind].neutral = 100.0     
    part_all = np.round_(part_all[:3]/part_all[3],2)
    message = [res_Content(**(x.dict())) for x in diar_result]
    print("clova sentiment done!")

    try:
        progress_4 = await progress_request(be_uri, fileName, user, 4)
        print(be_uri, "200 OK")
    except:
        print(be_uri, "ERR")
    # GPT summary
    try:
        sentence_all = "".join([x.message for x in diar_result])
        async with httpx.AsyncClient() as client:
            ct = "다음은 통화녹음을 음성으로 인식한 결과야, 이 대화를 요약할 수 있는 대표적인 단어들을, 그 개수를 10개 이내로 하여, 이를 쉼표로 구분하여 표현해줘.\n" + sentence_all
            tasks = [request(client, GPT_URI, json={"model" : "gpt-3.5-turbo",
                                                        "messages" :[{"role":"user", "content": ct}]},
                    header = GPT_HEADER)]
            result = await asyncio.gather(*tasks)
        summary = result[0]['choices'][0]['message']['content']
    except:
        summary = "에러나서 안돌아옴요."
    res_dict = {
        "fileName" : fileName,
        "user" : user,
        "speakerNum" : speakerNum,
        "length" : sound_len,
        "positive" : part_all[0],
        "negative" : part_all[1],
        "neutral" : part_all[2],
        "summary" : summary,
        "message" : message
    }
    return AudioResponse(**res_dict)

@app.post("/api/script")
async def sentiment(script:Script):
    # clova sentiment
    # 1회 호출시 최대 1000자 이므로 자르기

    sentences = script.script
    async with httpx.AsyncClient() as client:
        tasks = [request(client, CLOVA_URI, json={'content':st},
                header = CLOVA_HEADERS) for st in sentences]
        result = await asyncio.gather(*tasks)
    part_all = np.array([0.0, 0.0, 0.0, 0])
    for i in result:
        data = i['document']['confidence']
        part_all += [data['positive'], data['negative'], data['neutral'], 1]
        print(data['positive'], data['negative'], data['neutral'])
    part_all = np.round_(part_all[:3]/part_all[3],2)
    return {"positive" : part_all[0], "negative":part_all[1], "neutral":part_all[2]}



    