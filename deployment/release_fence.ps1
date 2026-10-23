# Existing producer protocols only; importing this file performs no operation.
function InitializeLibrarianNative {
 if('LocalBrainRelease.BarrierFile' -as [type]){return}
 Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace LocalBrainRelease {
 public static class BarrierFile {
  [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
  static extern SafeFileHandle CreateFileW(string name,uint access,uint share,IntPtr security,uint creation,uint flags,IntPtr template);
  [DllImport("kernel32.dll",SetLastError=true)]
  static extern bool SetFileInformationByHandle(SafeFileHandle handle,int info,ref byte data,uint size);
  public static FileStream Create(string path) {
   // DELETE access from creation; deny write/delete sharing for this exact file object.
   SafeFileHandle handle=CreateFileW(path,0xC0010000,1,IntPtr.Zero,1,0x80,IntPtr.Zero);
   if(handle.IsInvalid){int code=Marshal.GetLastWin32Error();handle.Dispose();throw new IOException("LIBRARIAN_CREATE_REFUSED",new Win32Exception(code));}
   try{return new FileStream(handle,FileAccess.ReadWrite);}catch{handle.Dispose();throw;}
  }
  public static void DeleteOwned(FileStream stream) {
   byte delete=1;
   if(!SetFileInformationByHandle(stream.SafeFileHandle,4,ref delete,1))
    throw new IOException("LIBRARIAN_HANDLE_DELETE_FAILED",new Win32Exception(Marshal.GetLastWin32Error()));
  }
 }
}
'@
}
function AcquireLibrarianBarrier($record){
 if($script:LibrarianBarrierStream -or $record.LibrarianBarrier){throw 'LIBRARIAN_BARRIER_ALREADY_USED'}
 InitializeLibrarianNative
 $path=SafePath (Join-Path $root 'runtime\librarian.lock') $root
 $token=[guid]::NewGuid().ToString()
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes((@{pid=$PID;started_at=[DateTime]::UtcNow.ToString('o');maintenance_token=$token}|ConvertTo-Json -Compress))
 try{$stream=[LocalBrainRelease.BarrierFile]::Create($path)}
 catch{throw 'LIBRARIAN_ACTIVE_OR_UNKNOWN_NO_STALE_REMOVAL'}
 try{
  $stream.Write($bytes);$stream.Flush($true)
  $record.LibrarianBarrier=@{path=$path;token=$token;pid=$PID;bytes_sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant();length=$bytes.Length;creation_filetime=(Get-Item -LiteralPath $path).CreationTimeUtc.ToFileTimeUtc().ToString();released=$false}
  $script:LibrarianBarrierStream=$stream
 }catch{
  # Even a partial creation is removed only through its original owned handle.
  try{[LocalBrainRelease.BarrierFile]::DeleteOwned($stream)}finally{$stream.Dispose()}
  throw 'LIBRARIAN_BARRIER_INITIALIZATION_FAILED'
 }
}
function AssertLibrarianBarrier($record){
 $b=$record.LibrarianBarrier;$stream=$script:LibrarianBarrierStream
 if(-not $b -or $b.released -or -not $stream -or -not $stream.CanWrite -or $stream.Length -ne $b.length -or
  (Get-Item -LiteralPath $b.path).CreationTimeUtc.ToFileTimeUtc().ToString() -ne $b.creation_filetime){throw 'LIBRARIAN_BARRIER_OWNERSHIP_UNKNOWN'}
 $bytes=[byte[]]::new([int]$stream.Length);$stream.Position=0;$offset=0;while($offset -lt $bytes.Length){$read=$stream.Read($bytes,$offset,$bytes.Length-$offset);if($read -le 0){throw 'LIBRARIAN_BARRIER_READ_FAILED'};$offset+=$read}
 if([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant() -ne $b.bytes_sha256){throw 'LIBRARIAN_BARRIER_BYTES_DRIFT'}
}
function ReleaseLibrarianBarrier($record){
 if(-not $script:LibrarianBarrierStream){return}
 $b=$record.LibrarianBarrier;$deleted=$false
 try{
  AssertLibrarianBarrier $record
  [LocalBrainRelease.BarrierFile]::DeleteOwned($script:LibrarianBarrierStream)
  $deleted=$true
 }finally{
  $script:LibrarianBarrierStream.Dispose();$script:LibrarianBarrierStream=$null
 }
 if(-not $deleted){throw 'LIBRARIAN_BARRIER_RELEASE_UNCONFIRMED'}
 # Do not delete by path after closing: any subsequent file belongs to another producer.
 $b.released=$true;$b.released_utc=[DateTime]::UtcNow.ToString('o')
}
function AcquireKnowledgeFence($record){
 AssertLibrarianBarrier $record
 if($script:KnowledgeFenceStream -or $record.KnowledgeFence){throw 'KNOWLEDGE_FENCE_ALREADY_USED'}
 $b=$intent.knowledge_lock_binding
 $path=SafePath (Join-Path $root 'runtime\knowledge-transaction.lock') $root
 if($b.path -ne 'runtime/knowledge-transaction.lock' -or -not (Test-Path -LiteralPath $path) -or
  (Get-Item -LiteralPath $path).Length -ne 1 -or
  (StringHash (Get-Acl -LiteralPath $path).Sddl) -ne $b.acl_sha256 -or
  (Get-Item -LiteralPath $path).CreationTimeUtc.ToFileTimeUtc().ToString() -ne $b.creation_filetime){throw 'KNOWLEDGE_LOCK_BINDING_DRIFT'}
 $stream=[IO.FileStream]::new($path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
 try{
  if($stream.Length -ne 1 -or (Get-Item -LiteralPath $path).CreationTimeUtc.ToFileTimeUtc().ToString() -ne $b.creation_filetime){throw 'KNOWLEDGE_LOCK_OPEN_IDENTITY_DRIFT'}
  $deadline=[Diagnostics.Stopwatch]::StartNew();$locked=$false
  do{
   try{$stream.Lock(0,1);$locked=$true;break}catch [IO.IOException]{Start-Sleep -Milliseconds 25}
  }while($deadline.Elapsed.TotalSeconds -lt 2)
  if(-not $locked){throw 'KNOWLEDGE_WRITER_BUSY'}
  $stream.Position=0;$byte=[byte[]]::new(1);if($stream.Read($byte,0,1) -ne 1 -or [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($byte)).ToLowerInvariant() -ne $b.sha256){throw 'KNOWLEDGE_LOCK_BYTES_DRIFT'}
  $script:KnowledgeFenceStream=$stream;$script:KnowledgeFenceClock=[Diagnostics.Stopwatch]::StartNew()
  $record.KnowledgeFence=@{path=$path;token=[guid]::NewGuid().ToString();acquired_utc=[DateTime]::UtcNow.ToString('o');cooperative_budget_seconds=20;released=$false}
 }catch{$stream.Dispose();throw}
}
function AssertKnowledgeFence($record){
 AssertLibrarianBarrier $record
 if(-not $script:KnowledgeFenceStream -or -not $script:KnowledgeFenceStream.CanWrite -or
  $script:KnowledgeFenceStream.Length -ne 1 -or -not $record.KnowledgeFence -or $record.KnowledgeFence.released){throw 'KNOWLEDGE_FENCE_NOT_OWNED'}
 if($script:KnowledgeFenceClock.Elapsed.TotalSeconds -gt 20){throw 'KNOWLEDGE_FENCE_COOPERATIVE_BUDGET_EXPIRED'}
}
function ReleaseKnowledgeFence($record){
 if(-not $script:KnowledgeFenceStream){return}
 try{$script:KnowledgeFenceStream.Unlock(0,1)}finally{
  $script:KnowledgeFenceStream.Dispose();$script:KnowledgeFenceStream=$null
  if($record.KnowledgeFence){$record.KnowledgeFence.released=$true;$record.KnowledgeFence.released_utc=[DateTime]::UtcNow.ToString('o');$record.KnowledgeFence.held_seconds=$script:KnowledgeFenceClock.Elapsed.TotalSeconds}
  $script:KnowledgeFenceClock=$null
 }
}
