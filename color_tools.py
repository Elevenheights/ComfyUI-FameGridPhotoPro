"""Float32 HSL color mixing and a color/area skin selection, after native develop."""
import numpy as np

CHANNELS = ('red', 'orange', 'yellow', 'green', 'aqua', 'blue', 'purple', 'magenta')
CENTERS = (0, 30, 60, 120, 180, 240, 270, 300)


def validate(raw, number):
    mixer = raw.get('colorMixer', {})
    skin = raw.get('skinTone', {})
    if not isinstance(mixer, dict) or not isinstance(skin, dict):
        raise ValueError('Invalid color mixer or skin tone settings.')
    channels = mixer.get('channels', {})
    if not isinstance(channels, dict): raise ValueError('Invalid color mixer channels.')
    normalized = {}
    for name in CHANNELS:
        values = channels.get(name, {})
        if not isinstance(values, dict): raise ValueError('Invalid color channel: '+name)
        normalized[name] = {k: number(values.get(k, 0), -100, 100, name+' '+k) for k in ('hue','saturation','lightness')}
    mixer = dict(enabled=mixer.get('enabled', False), channels=normalized)
    result = dict(enabled=skin.get('enabled', False))
    for key, default, low, high in [('hue',0,-100,100),('saturation',0,-100,100),('lightness',0,-100,100),
                                    ('center',30,0,360),('width',30,1,120),('feather',15,0,60),
                                    ('minSaturation',.08,0,1),('minLightness',.02,0,1),('maxLightness',.98,0,1)]:
        result[key] = number(skin.get(key, default), low, high, 'skin '+key)
    region = skin.get('region', dict(x=0,y=0,width=1,height=1))
    if not isinstance(region, dict): raise ValueError('Invalid skin selection area.')
    result['region'] = {k:number(region.get(k),0,1,'skin area '+k) for k in ('x','y','width','height')}
    r = result['region']
    if not r['width'] or not r['height'] or r['x']+r['width']>1.000001 or r['y']+r['height']>1.000001:
        raise ValueError('Skin selection area must stay inside the image.')
    if result['minLightness']>=result['maxLightness']: raise ValueError('Skin lightness range is empty.')
    if not isinstance(mixer['enabled'],bool) or not isinstance(result['enabled'],bool): raise ValueError('Invalid color tool toggle.')
    return mixer, result


def rgb_to_hsl(rgb):
    high=rgb.max(axis=-1);low=rgb.min(axis=-1);delta=high-low;light=(high+low)*.5
    sat=np.divide(delta,1-np.abs(2*light-1),out=np.zeros_like(delta),where=delta>1e-7)
    safe=np.where(delta>1e-7,delta,1)
    hue=np.where(high==rgb[...,0],np.mod((rgb[...,1]-rgb[...,2])/safe,6),
                 np.where(high==rgb[...,1],(rgb[...,2]-rgb[...,0])/safe+2,(rgb[...,0]-rgb[...,1])/safe+4))*60
    hue=np.where(delta>1e-7,hue,0)
    return hue.astype(np.float32),sat.astype(np.float32),light.astype(np.float32)


def hsl_to_rgb(hue,sat,light):
    chroma=(1-np.abs(2*light-1))*sat;sector=np.mod(hue/60,6)
    x=chroma*(1-np.abs(np.mod(sector,2)-1));zero=np.zeros_like(chroma)
    i=sector.astype(np.int32)
    r=np.select([i==0,i==1,i==2,i==3,i==4],[chroma,x,zero,zero,x],default=chroma)
    g=np.select([i==0,i==1,i==2,i==3,i==4],[x,chroma,chroma,x,zero],default=zero)
    b=np.select([i==0,i==1,i==2,i==3,i==4],[zero,zero,x,chroma,chroma],default=x)
    return np.clip(np.stack((r,g,b),axis=-1)+(light-chroma*.5)[...,None],0,1).astype(np.float32)


def smooth(value):
    value=np.clip(value,0,1)
    return value*value*(3-2*value)


def skin_mask(hue,sat,light,skin,row,height,width):
    distance=np.abs(np.mod(hue-skin['center']+180,360)-180)
    core=skin['width']*.5;feather=skin['feather']
    mask=(distance<=core).astype(np.float32) if not feather else 1-smooth((distance-core)/feather)
    mask*=smooth((sat-skin['minSaturation'])/.05)
    mask*=smooth((light-skin['minLightness'])/.03)*smooth((skin['maxLightness']-light)/.03)
    r=skin['region'];x=(np.arange(width,dtype=np.float32)+.5)/width
    y=(np.arange(row,row+hue.shape[0],dtype=np.float32)+.5)/height
    horizontal=((x>=r['x'])&(x<=r['x']+r['width'])).astype(np.float32)
    vertical=((y>=r['y'])&(y<=r['y']+r['height'])).astype(np.float32)
    if r['x']>0:horizontal*=smooth((x-r['x'])/.02)
    if r['x']+r['width']<.999999:horizontal*=smooth((r['x']+r['width']-x)/.02)
    if r['y']>0:vertical*=smooth((y-r['y'])/.02)
    if r['y']+r['height']<.999999:vertical*=smooth((r['y']+r['height']-y)/.02)
    return mask*vertical[:,None]*horizontal[None,:]


def shift(hue,sat,light,dh,ds,dl):
    hue=np.mod(hue+dh*.3,360)
    sat=np.clip(sat+np.where(ds>=0,1-sat,sat)*ds/100,0,1)
    light=np.clip(light+np.where(dl>=0,1-light,light)*dl/100,0,1)
    return hue,sat,light


def apply(rgb,alpha,mixer,skin,mask_preview=False,cancel=lambda:None):
    mixed=mixer['enabled'] and any(v for values in mixer['channels'].values() for v in values.values())
    skinned=skin['enabled'] and any(skin[k] for k in ('hue','saturation','lightness'))
    if not mixed and not skinned and not mask_preview:return rgb
    output=np.empty_like(rgb,dtype=np.float32);height,width=rgb.shape[:2]
    centers=np.array((*CENTERS,360),dtype=np.float32)
    for row in range(0,height,128):
        cancel();source=rgb[row:row+128];h,s,l=rgb_to_hsl(np.clip(source,0,1))
        mask=skin_mask(h,s,l,skin,row,height,width) if skinned or mask_preview else None
        if mask_preview:
            output[row:row+128]=np.repeat((mask*(alpha[row:row+128]>0))[...,None],3,axis=-1)
            continue
        if mixed:
            values=[]
            for key in ('hue','saturation','lightness'):
                points=[mixer['channels'][name][key] for name in CHANNELS]
                values.append(np.interp(h,centers,points+[points[0]]).astype(np.float32)*smooth(s/.05))
            h,s,l=shift(h,s,l,*values)
            active=np.logical_or.reduce([v!=0 for v in values])
            result=np.where(active[...,None],hsl_to_rgb(h,s,l),source)
        else:result=source.copy()
        if skinned:
            target=hsl_to_rgb(*shift(h,s,l,skin['hue'],skin['saturation'],skin['lightness']))
            result=result*(1-mask[...,None])+target*mask[...,None]
        output[row:row+128]=result
    return output
