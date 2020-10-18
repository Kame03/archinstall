import archinstall
import getpass, time, json, sys, signal

"""
This signal-handler chain (and global variable)
is used to trigger the "Are you sure you want to abort?" question.
"""
SIG_TRIGGER = False
def kill_handler(sig, frame):
	print()
	exit(0)
def sig_handler(sig, frame):
	global SIG_TRIGGER
	SIG_TRIGGER = True
	signal.signal(signal.SIGINT, kill_handler)
signal.signal(signal.SIGINT, sig_handler)

def perform_installation(device, boot_partition, language, mirrors):
	"""
	Performs the installation steps on a block device.
	Only requirement is that the block devices are
	formatted and setup prior to entering this function.
	"""
	with archinstall.Installer(device, boot_partition=boot_partition, hostname=hostname) as installation:
		## if len(mirrors):
		# Certain services might be running that affects the system during installation.
		# Currently, only one such service is "reflector.service" which updates /etc/pacman.d/mirrorlist
		# We need to wait for it before we continue since we opted in to use a custom mirror/region.
		archinstall.log(f'Waiting for automatic mirror selection has completed before using custom mirrors.')
		while 'dead' not in (status := archinstall.service_state('reflector')):
			time.sleep(1)

		archinstall.use_mirrors(mirrors) # Set the mirrors for the live medium
		if installation.minimal_installation():
			installation.set_mirrors(mirrors) # Set the mirrors in the installation medium
			installation.set_keyboard_language(language)
			installation.add_bootloader()

			if len(packages) and packages[0] != '':
				installation.add_additional_packages(packages)

			if profile and len(profile.strip()):
				installation.install_profile(profile)

			for user, password in users.items():
				sudo = False
				if len(root_pw.strip()) == 0:
					sudo = True

				installation.user_create(user, password, sudo=sudo)

			if root_pw:
				installation.user_set_pw('root', root_pw)

# Unmount and close previous runs (in case the installer is restarted)
archinstall.sys_command(f'umount -R /mnt', surpress_errors=True)
archinstall.sys_command(f'cryptsetup close /dev/mapper/luksloop', surpress_errors=True)

"""
  First, we'll ask the user for a bunch of user input.
  Not until we're satisfied with what we want to install
  will we continue with the actual installation steps.
"""

keyboard_language = archinstall.select_language(archinstall.list_keyboard_languages())
archinstall.set_keyboard_language(keyboard_language)

# Create a storage structure for all our information.
# We'll print this right before the user gets informed about the formatting timer.
archinstall.storage['_guided'] = {}

# Set which region to download packages from during the installation
mirror_regions = archinstall.select_mirror_regions(archinstall.list_mirrors())
archinstall.storage['_guided']['mirrors'] = mirror_regions

# Ask which harddrive/block-device we will install to
harddrive = archinstall.select_disk(archinstall.all_disks())
while (disk_password := getpass.getpass(prompt='Enter disk encryption password (leave blank for no encryption): ')):
	disk_password_verification = getpass.getpass(prompt='And one more time for verification: ')
	if disk_password != disk_password_verification:
		archinstall.log(' * Passwords did not match * ', bg='black', fg='red')
		continue
	break
archinstall.storage['_guided']['harddrive'] = harddrive

# Ask for a hostname
hostname = input('Desired hostname for the installation: ')
if len(hostname) == 0: hostname = 'ArchInstall'
archinstall.storage['_guided']['hostname'] = hostname

# Ask for a root password (optional, but triggers requirement for super-user if skipped)
while (root_pw := getpass.getpass(prompt='Enter root password (leave blank to leave root disabled): ')):
	root_pw_verification = getpass.getpass(prompt='And one more time for verification: ')
	if root_pw != root_pw_verification:
		archinstall.log(' * Passwords did not match * ', bg='black', fg='red')
		continue
	break

# Ask for additional users (super-user if root pw was not set)
users = {}
new_user_text = 'Any additional users to install (leave blank for no users): '
if len(root_pw.strip()) == 0:
	new_user_text = 'Create a super-user with sudo privileges: '

while 1:
	new_user = input(new_user_text)
	if len(new_user.strip()) == 0:
		if len(root_pw.strip()) == 0:
			archinstall.log(' * Since root is disabled, you need to create a least one (super) user!', bg='black', fg='red')
			continue
		break

	if 'users' not in archinstall.storage['_guided']: archinstall.storage['_guided']['users'] = []
	archinstall.storage['_guided']['users'].append(new_user)

	new_user_passwd = getpass.getpass(prompt=f'Password for user {new_user}: ')
	new_user_passwd_verify = getpass.getpass(prompt=f'Enter password again for verification: ')
	if new_user_passwd != new_user_passwd_verify:
		archinstall.log(' * Passwords did not match * ', bg='black', fg='red')
		continue

	users[new_user] = new_user_passwd
	break

# Ask for archinstall-specific profiles (such as desktop environments etc)
while 1:
	profile = archinstall.select_profile(archinstall.list_profiles())
	if profile:
		archinstall.storage['_guided']['profile'] = profile

		if type(profile) != str: # Got a imported profile
			archinstall.storage['_guided']['profile'] = profile[0] # The second return is a module, and not a handle/object.
			if not profile[1]._prep_function():
				archinstall.log(' * Profile\'s preperation requirements was not fulfilled.', bg='black', fg='red')
				continue

			profile = profile[0]._path # Once the prep is done, replace the selected profile with the profile name ("path") given from select_profile()
			break
	else:
		break

# Additional packages (with some light weight error handling for invalid package names)
while 1:
	packages = [package for package in input('Additional packages aside from base (space separated): ').split(' ') if len(package)]

	if not packages:
		break

	try:
		if archinstall.validate_package_list(packages):
			archinstall.storage['_guided']['packages'] = packages
			break
	except archinstall.RequirementError as e:
		print(e)

print()
print('This is your chosen configuration:')
print(json.dumps(archinstall.storage['_guided'], indent=4, sort_keys=True, cls=archinstall.JSON))
print()

"""
	Issue a final warning before we continue with something un-revertable.
	We mention the drive one last time, and count from 5 to 0.
"""

print(f' ! Formatting {harddrive} in ', end='')

for i in range(5, 0, -1):
	print(f"{i}", end='')

	for x in range(4):
		sys.stdout.flush()
		time.sleep(0.25)
		print(".", end='')

	if SIG_TRIGGER:
		abort = input('\nDo you really want to abort (y/n)? ')
		if abort.strip() != 'n':
			exit(0)

		if SIG_TRIGGER is False:
			sys.stdin.read()
		SIG_TRIGGER = False
		signal.signal(signal.SIGINT, sig_handler)
print()
"""
	Setup the blockdevice, filesystem (and optionally encryption).
	Once that's done, we'll hand over to perform_installation()
"""
with archinstall.Filesystem(harddrive, archinstall.GPT) as fs:
	# Use partitioning helper to set up the disk partitions.
	if disk_password:
		fs.use_entire_disk('luks2')
	else:
		fs.use_entire_disk('ext4')

	if harddrive.partition[1].size == '512M':
		raise OSError('Trying to encrypt the boot partition for petes sake..')
	harddrive.partition[0].format('fat32')

	if disk_password:
		# First encrypt and unlock, then format the desired partition inside the encrypted part.
		# archinstall.luks2() encrypts the partition when entering the with context manager, and
		# unlocks the drive so that it can be used as a normal block-device within archinstall.
		with archinstall.luks2(harddrive.partition[1], 'luksloop', disk_password) as unlocked_device:
			unlocked_device.format('btrfs')
			
			perform_installation(unlocked_device, harddrive.partition[0], keyboard_language, mirror_regions)
	else:
		harddrive.partition[1].format('ext4')
		perform_installation(harddrive.partition[1], harddrive.partition[0], keyboard_language, mirror_regions)