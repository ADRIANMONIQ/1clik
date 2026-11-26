Option Explicit

Dim shell, command
Set shell = CreateObject("WScript.Shell")
command = "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\Users\Vaio\Documents\1Clik\iniciar_1clik_automatico.ps1"""

' Window style 0 guarantees that the recurring health check never flashes a console.
shell.Run command, 0, True
