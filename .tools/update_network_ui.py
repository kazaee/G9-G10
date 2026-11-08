from pathlib import Path
p=Path('ui/index.html')
s=p.read_text(encoding='utf-8')
s=s.replace("new Option('انتخاب کارت از نتایج زنده','')", "new Option('تشخیص خودکار کارت متصل','')")
old="$('review-network').textContent=JSON.stringify({network:s.review.network,username:s.review.username,credentials_file:s.review.credentials_file},null,2);"
new="const n=s.review.network;$('review-network').textContent=['IPv4: '+n.address,'Gateway: '+n.gateway,'DNS: '+n.dns.join(', '),'MAC: '+(n.mac||'تشخیص خودکار پس از برقرارشدن لینک'),'Username: '+s.review.username,'Credentials: '+(s.review.credentials_file||'رمز انتخاب‌شده توسط شما')].join('\\n');"
assert old in s
s=s.replace(old,new)
p.write_text(s,encoding='utf-8')

p=Path('README.md');s=p.read_text()
start=s.index('## Current hardware test result')
end=s.index('## Run',start)
s=s[:start]+'''## Current hardware test result (2026-10-01)

The current application-driven installation test is recorded in
`generated/application-install-test.json`. Its installation run is
`logs/2026-10-01_20-53-21/`; this run is in progress until its final result and
installed-boot evidence explicitly confirm completion. Local tests: 35 passed.

The completed live discovery at `logs/2026-10-01_15-20-50/` confirmed server
HZ75NU7565, both RAID logical volumes, all four physical members, and UEFI boot.
All six NICs were brought administratively UP but reported no physical carrier.
The requested network is 193.141.127.102/24, gateway 193.141.127.254, DNS 4.2.2.4.
The operator authorized installation without network reachability. Only the
931.48 GiB SSD RAID volume is selected for erasure; the 4 TB HDD RAID stays out
of the curtin disk actions. Networking remains pending until a unique connected
port can be selected.

Earlier read-only tests found an existing Ubuntu 24.04.3 on `/dev/sda2`, hostname
`ubuntu-server`, with `ubuntu` and `root` accounts. Its installer log was dated
2026-09-26 UTC, before this project's first recorded run. This was the old OS,
not evidence of a successful installation by this tool. The new installation
uses hostname `ubuntu-test`, account `provision`, and an LVM root filesystem.

''' +s[end:]
p.write_text(s)
