/** 前端分句：给「逐句实时播报」用，与后端 long_tts.split_text 的规则保持一致。 */
export function splitSentences(input: string, maxChars = 120, maxSentences = 40): string[] {
  const pieces = input.match(/[^。！？!?；;\n]+[。！？!?；;\n]?/g) || []
  const result: string[] = []
  for (const piece of pieces) {
    let rest = piece.trim()
    if (!rest) continue
    while (rest.length > maxChars) {
      result.push(rest.slice(0, maxChars))
      rest = rest.slice(maxChars)
    }
    if (rest) result.push(rest)
  }
  return result.slice(0, maxSentences)
}
