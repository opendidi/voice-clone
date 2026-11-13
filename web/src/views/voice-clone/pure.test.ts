import { describe, expect, it } from 'vitest'
import { analyzeQuality, encodeWav, formatBytes, formatDuration, limitsFromStatus } from './audio'
import { splitSentences } from './text'

function fakeBuffer(frames: Float32Array[], rate = 48000): AudioBuffer {
  return {
    numberOfChannels: frames.length,
    length: frames[0].length,
    sampleRate: rate,
    duration: frames[0].length / rate,
    getChannelData: (index: number) => frames[index],
  } as unknown as AudioBuffer
}

function ascii(view: DataView, offset: number, length: number): string {
  return Array.from({ length }, (_, i) => String.fromCharCode(view.getUint8(offset + i))).join('')
}

describe('encodeWav', () => {
  it('写出 16-bit PCM / 48kHz 的标准 RIFF 头', async () => {
    const blob = encodeWav(fakeBuffer([new Float32Array([0, 0.5, -0.5, 1, -1])]))
    const view = new DataView(await blob.arrayBuffer())
    expect(ascii(view, 0, 4)).toBe('RIFF')
    expect(ascii(view, 8, 4)).toBe('WAVE')
    expect(ascii(view, 36, 4)).toBe('data')
    expect(view.getUint16(20, true)).toBe(1)
    expect(view.getUint16(22, true)).toBe(1)
    expect(view.getUint32(24, true)).toBe(48000)
    expect(view.getUint16(34, true)).toBe(16)
    expect(view.getUint32(40, true)).toBe(10)
    expect(blob.size).toBe(54)
    expect(view.getInt16(46, true)).toBe(16383)
  })

  it('双声道交错存储', async () => {
    const blob = encodeWav(fakeBuffer([
      new Float32Array([0.5, -0.5]),
      new Float32Array([1, -1]),
    ]))
    const view = new DataView(await blob.arrayBuffer())
    expect(view.getUint16(22, true)).toBe(2)
    expect(view.getUint32(40, true)).toBe(8)
    expect(view.getInt16(44, true)).toBe(16383)
    expect(view.getInt16(46, true)).toBe(32767)
  })
})

describe('analyzeQuality', () => {
  it('识别静音音频', () => {
    const q = analyzeQuality(fakeBuffer([new Float32Array(1000)]))
    expect(q.rms).toBe(0)
    expect(q.silent).toBe(true)
    expect(q.warning).toContain('静音')
  })

  it('识别削波音频', () => {
    const q = analyzeQuality(fakeBuffer([new Float32Array(1000).fill(1)]))
    expect(q.peak).toBe(1)
    expect(q.clipping).toBe(true)
    expect(q.warning).toContain('削波')
  })

  it('正常音量不告警', () => {
    const q = analyzeQuality(fakeBuffer([new Float32Array(1000).fill(0.1)]))
    expect(q.silent).toBe(false)
    expect(q.clipping).toBe(false)
    expect(q.warning).toBe('')
  })
})

describe('format helpers', () => {
  it('格式化字节与时长', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatDuration(1.26)).toBe('1.3s')
    expect(formatDuration(-1)).toBe('0.0s')
  })

  it('状态缺省时返回内置限制', () => {
    expect(limitsFromStatus(null).maxTextChars).toBe(1000)
    expect(limitsFromStatus(null).longTtsMaxChars).toBe(5000)
  })
})

describe('splitSentences', () => {
  it('按中英文标点断句', () => {
    expect(splitSentences('第一句。第二句！Third?')).toEqual(['第一句。', '第二句！', 'Third?'])
    expect(splitSentences('')).toEqual([])
  })

  it('超长句按字数硬切且不超过上限', () => {
    const pieces = splitSentences('啊'.repeat(300))
    expect(pieces.length).toBe(3)
    expect(pieces.every((p) => p.length <= 120)).toBe(true)
    expect(pieces.join('').length).toBe(300)
  })

  it('限制最大句数', () => {
    expect(splitSentences('好。'.repeat(100), 120, 10).length).toBe(10)
  })
})
