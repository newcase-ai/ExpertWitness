import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Send, Loader2 } from 'lucide-react'

type ChatInputProps = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  placeholder?: string
  disabled?: boolean
  loading?: boolean
}

export default function ChatInput({
  value,
  onChange,
  onSend,
  placeholder = 'Ask a question...',
  disabled = false,
  loading = false,
}: ChatInputProps) {
  return (
    <div className="relative">
      <Input
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            onSend()
          }
        }}
        disabled={disabled}
        className="h-10 text-sm pr-10 bg-muted/30 border-muted focus-visible:bg-background transition-colors"
      />
      <Button
        size="sm"
        onClick={onSend}
        disabled={disabled || !value.trim()}
        className="absolute right-1 top-1 h-8 w-8 p-0 rounded-md"
        variant="ghost"
      >
        {loading ? (
          <Loader2 className="h-4 w-4 text-muted-foreground animate-spin" />
        ) : (
          <Send className="h-4 w-4 text-primary" />
        )}
      </Button>
    </div>
  )
}
