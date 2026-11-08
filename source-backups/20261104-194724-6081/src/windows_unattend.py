"""Test-only Windows Server 2022 image deployment via original DVD and iLO USB."""
import ipaddress
import re
import string
from xml.sax.saxutils import escape
from .windows_postinstall import render as render_postinstall


def render(disk, other_disks, mac, address, gateway, dns, hostname, password, image_index=2,
           backup_password=None, data_disk=None, report_url=None, rdp_port=33096):
    if image_index not in (1, 2, 3, 4):
        raise ValueError('Unknown Windows Server 2022 Evaluation WIM index')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,14}', hostname):
        raise ValueError('Windows computer name must be 1–15 ASCII letters, numbers or hyphens')
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac.lower()):
        raise ValueError('Choose a verified Windows network MAC')
    if not password or len(password) < 12 or any(ord(c) < 32 for c in password):
        raise ValueError('Windows Administrator password must contain at least 12 printable characters')
    classes = sum(any(c in group for c in password) for group in
                  (string.ascii_lowercase, string.ascii_uppercase,
                   string.digits, string.punctuation))
    if classes < 3 or 'administrator' in password.lower():
        raise ValueError('Windows Administrator password needs three character types and must not contain the account name')
    interface = ipaddress.ip_interface(address)
    gateway_ip = ipaddress.ip_address(gateway)
    if interface.version != 4 or gateway_ip.version != 4 or gateway_ip not in interface.network:
        raise ValueError('Windows IPv4 gateway must be in the selected subnet')
    dns_ips = [str(ipaddress.ip_address(item)) for item in dns]
    if not dns_ips:
        raise ValueError('Windows DNS is required')
    if not backup_password:
        raise ValueError('A separate recovery administrator password is required')
    if data_disk is not None:
        if data_disk not in other_disks or data_disk is disk:
            raise ValueError('Windows data disk must be a separate reviewed disk')
        data_size = int(data_disk['size'])
        if any(other is not data_disk and abs(int(other['size']) - data_size) <= 128 * 1024**2
               for other in [disk, *other_disks]):
            raise ValueError('Windows cannot safely distinguish equal-sized data disks')
    postinstall = render_postinstall(mac, address, gateway, dns_ips, backup_password,
        data_disk_size=data_disk['size'] if data_disk is not None else None,
        report_url=report_url, rdp_port=rdp_port)
    size = int(disk['size'])
    if size < 40 * 1024**3:
        raise ValueError('Windows installation disk must be at least 40 GiB')
    # The WinPE script discovers the Windows disk number by this unique size.
    # If another drive is close enough, refuse before building destructive media.
    margin = 64 * 1024**2
    if any(abs(int(other['size']) - size) <= 2 * margin for other in other_disks):
        raise ValueError('Windows cannot safely distinguish equal-sized disks; a serial-aware preflight is needed')
    lower, upper = size - margin, size + margin
    os_mib = size // 1024**2 - 260 - 16 - 2048 - 64
    if os_mib < 30 * 1024:
        raise ValueError('Windows OS partition would be too small')
    component = 'processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS"'
    wcm = 'xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State"'
    starter = f'''<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" {wcm}>
  <settings pass="windowsPE"><component name="Microsoft-Windows-Setup" {component}>
    <UserData><AcceptEula>true</AcceptEula></UserData>
    <RunSynchronous><RunSynchronousCommand {wcm}>
      <Order>1</Order><Path>cmd.exe /c for %D in (A B C D E F G H I J K L M N O P Q R S T U V W Y Z) do @if exist %D:\\DISKPREP.CMD call %D:\\DISKPREP.CMD</Path>
      <Description>Verify selected RAID disk, deploy image and reboot</Description>
    </RunSynchronousCommand></RunSynchronous>
  </component></settings>
</unattend>
'''
    dns_rows = ''.join(f'<IpAddress wcm:action="add" wcm:keyValue="{n}">{escape(ip)}</IpAddress>'
                       for n, ip in enumerate(dns_ips, 1))
    windows_mac = mac.replace(':', '-').upper()
    firstboot = f'''<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" {wcm}>
 <settings pass="specialize">
  <component name="Microsoft-Windows-Shell-Setup" {component}><ComputerName>{escape(hostname)}</ComputerName></component>
  <component name="Microsoft-Windows-TCPIP" {component}>
   <Interfaces><Interface wcm:action="add"><Ipv4Settings><DhcpEnabled>false</DhcpEnabled></Ipv4Settings>
    <Identifier>{windows_mac}</Identifier>
    <UnicastIpAddresses><IpAddress wcm:action="add" wcm:keyValue="1">{interface}</IpAddress></UnicastIpAddresses>
    <Routes><Route wcm:action="add"><Identifier>1</Identifier><Metric>10</Metric><NextHopAddress>{gateway_ip}</NextHopAddress><Prefix>0.0.0.0/0</Prefix></Route></Routes>
   </Interface></Interfaces>
  </component>
  <component name="Microsoft-Windows-DNS-Client" {component}>
   <Interfaces><Interface wcm:action="add"><Identifier>{windows_mac}</Identifier><DNSServerSearchOrder>{dns_rows}</DNSServerSearchOrder></Interface></Interfaces>
  </component>
  <component name="Microsoft-Windows-Deployment" {component}>
   <RunSynchronous><RunSynchronousCommand wcm:action="add">
    <Order>1</Order><Description>Configure Windows before first logon</Description>
    <Path>cmd.exe /c %WINDIR%\\Setup\\Scripts\\SetupComplete.cmd</Path>
   </RunSynchronousCommand></RunSynchronous>
  </component>
 </settings>
 <settings pass="oobeSystem">
  <component name="Microsoft-Windows-International-Core" {component}>
   <InputLocale>en-US</InputLocale><SystemLocale>en-US</SystemLocale><UILanguage>en-US</UILanguage><UserLocale>en-US</UserLocale>
  </component>
  <component name="Microsoft-Windows-Shell-Setup" {component}>
   <UserAccounts><AdministratorPassword><Value>{escape(password)}</Value><PlainText>true</PlainText></AdministratorPassword></UserAccounts>
   <OOBE><HideEULAPage>true</HideEULAPage><ProtectYourPC>3</ProtectYourPC></OOBE>
   <FirstLogonCommands><SynchronousCommand wcm:action="add">
    <Order>1</Order><Description>Finish server provisioning</Description>
    <CommandLine>cmd.exe /c %WINDIR%\\Setup\\Scripts\\SetupComplete.cmd</CommandLine>
   </SynchronousCommand></FirstLogonCommands>
  </component>
 </settings>
</unattend>
'''
    batch = f'''@echo off
setlocal EnableDelayedExpansion
set "ANSWER=%~d0"
set "STEP=locate wmic in WinPE"
wmic.exe /? >nul 2>&1
if errorlevel 1 goto fail
set "STEP=identify unique installation disk"
for /l %%N in (1,1,6) do (
  set "COUNT=0"
  set "TARGET="
  for /f "tokens=1,2 delims==" %%I in ('wmic diskdrive where "Size>{lower} AND Size<{upper}" get Index /value') do (
    if /I "%%I"=="Index" (
      set /a COUNT+=1
      set /a TARGET=%%J >nul
    )
  )
  if "!COUNT!"=="1" goto founddisk
  if !COUNT! GTR 1 goto fail
)
:founddisk
if not "!COUNT!"=="1" goto fail
set "WIM="
set "STEP=locate install.wim on Windows DVD"
for /l %%N in (1,1,12) do (
  for %%D in (A B C D E F G H I J K L M N O P Q R S T U V W Y Z) do if exist %%D:\\sources\\install.wim set "WIM=%%D:\\sources\\install.wim"
  if defined WIM goto foundwim
)
:foundwim
if not defined WIM goto fail
if exist S:\\ goto occupiedletters
if exist W:\\ goto occupiedletters
set "STEP=partition selected disk !TARGET!"
echo select disk !TARGET! > X:\\layout.txt
echo clean >> X:\\layout.txt
echo convert gpt >> X:\\layout.txt
echo create partition efi size=260 >> X:\\layout.txt
echo format quick fs=fat32 label=System >> X:\\layout.txt
echo assign letter=S >> X:\\layout.txt
echo create partition msr size=16 >> X:\\layout.txt
echo create partition primary size={os_mib} >> X:\\layout.txt
echo format quick fs=ntfs label=Windows >> X:\\layout.txt
echo assign letter=W >> X:\\layout.txt
echo create partition primary size=2048 >> X:\\layout.txt
echo format quick fs=ntfs label=Recovery >> X:\\layout.txt
echo set id=de94bba4-06d1-4d40-a16a-bfd50179d6ac >> X:\\layout.txt
echo gpt attributes=0x8000000000000001 >> X:\\layout.txt
diskpart /s X:\\layout.txt > X:\\provisioning-diskpart.log 2>&1
if errorlevel 1 goto fail
if not exist W:\\ goto fail
if not exist S:\\ goto fail
set "STEP=apply Windows image"
dism /Apply-Image /ImageFile:"!WIM!" /Index:{image_index} /ApplyDir:W:\\ /CheckIntegrity
if errorlevel 1 goto fail
if not exist W:\\Windows\\System32\\config\\SYSTEM goto fail
if not exist W:\\Windows\\Panther mkdir W:\\Windows\\Panther
set "STEP=copy first-boot answer file"
copy /y "!ANSWER!\\FIRSTBOOT.XML" W:\\Windows\\Panther\\unattend.xml >nul
if errorlevel 1 goto fail
set "STEP=stage Windows post-install script"
if not exist W:\\Windows\\Setup\\Scripts mkdir W:\\Windows\\Setup\\Scripts
copy /y "!ANSWER!\\POSTINST.PS1" W:\\Windows\\Setup\\Scripts\\Provision-PostInstall.ps1 >nul
if errorlevel 1 goto fail
copy /y "!ANSWER!\\SETUPCOMP.CMD" W:\\Windows\\Setup\\Scripts\\SetupComplete.cmd >nul
if errorlevel 1 goto fail
set "STEP=write UEFI boot files"
bcdboot W:\\Windows /s S: /f UEFI
if errorlevel 1 goto fail
echo Windows image applied to disk !TARGET! > W:\\provisioning-result.txt
wpeutil reboot
exit /b 0
:fail
echo WINDOWS PROVISIONING FAILED at !STEP!. Disk match count: !COUNT!. > X:\\provisioning-failure.txt
type X:\\provisioning-failure.txt
if exist X:\\provisioning-diskpart.log type X:\\provisioning-diskpart.log
echo WinPE remains open for diagnosis. Do not reboot until the error is recorded.
cmd.exe /k
exit /b 1
:occupiedletters
set "STEP=drive letters S or W are already in use before partitioning"
goto fail
'''
    return {'Autounattend.xml': starter, 'DISKPREP.CMD': batch, 'FIRSTBOOT.XML': firstboot,
            **postinstall}
